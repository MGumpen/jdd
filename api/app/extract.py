"""Steg 3: uttrekk av produkt, problem og løsning fra en anonymisert e-posttråd.

To moduser:
- KI-modus (ANTHROPIC_API_KEY satt): modellen leser tråden og svarer med JSON.
- Regelmodus: finner SKU/produktnavn i teksten, bruker kundens første avsnitt
  som problem og kundeservicens svar som løsning.
Feiler KI-kallet, brukes regelmodus som reserve.
"""

import logging
import re
from dataclasses import dataclass

from . import config, llm
from .anonymize import anonymize_rules

log = logging.getLogger(__name__)

# Slik ser en tråd ut: svaret fra kundeservice øverst, kundens opprinnelige
# henvendelse under denne linjen (vanlig i e-postsvar).
THREAD_SEPARATOR = "----- Opprinnelig melding -----"

SKU_PATTERN = re.compile(r"\bDEMO-\d{3}\b")
PLACEHOLDER = re.compile(r"\[(?:NAVN|E-POST|TELEFON|ORDRE|ADRESSE|REGNR)\]")
GREETING = re.compile(r"^(hei|hallo|heisann|kjære|god dag)\b", re.IGNORECASE)
SIGN_OFF = re.compile(r"^(mvh|med vennlig hilsen|vennlig hilsen|beste hilsen|hilsen)\b", re.IGNORECASE)
SUBJECT_PREFIX = re.compile(r"^\s*((sv|re|vs|fw|fwd)\s*:\s*)+", re.IGNORECASE)

# Enkle stikkord for regelmodus: ordstamme i teksten -> tagg.
KEYWORD_TAGS = {
    "flimr": "flimring", "dugg": "dugg", "kabelsett": "kabelsett", "relé": "relé",
    "sikring": "sikring", "jord": "jording", "stripe": "striper", "monter": "montering",
    "blink": "blinklys", "radio": "radiostøy", "adapter": "adapter", "ryggelys": "ryggelys",
    "voks": "voks", "lakk": "lakk", "tilheng": "tilhenger",
}


@dataclass
class Extraction:
    sku: str | None
    title: str
    problem: str
    solution: str
    tags: str
    mode: str          # "KI" eller "regel"
    note: str = ""     # f.eks. hvorfor KI-modus falt tilbake til regler


def extract(text: str, products: list) -> Extraction:
    """Velger KI-modus eller regelmodus. `products` er en liste med Product-objekter."""
    if config.ai_enabled():
        try:
            return sanitize(extract_with_ai(text, products))
        except Exception as exc:  # noqa: BLE001 – regelmodus er alltid reserve
            log.warning("KI-uttrekk feilet, bruker regelmodus: %s", exc)
            result = extract_with_rules(text, products)
            result.note = f"KI-uttrekk feilet ({type(exc).__name__}), brukte regelmodus."
            return sanitize(result)
    return sanitize(extract_with_rules(text, products))


# ---------------------------------------------------------------- KI-modus

_AI_SYSTEM = (
    "Du hjelper kundesenteret i JDD Utstyr (bilbelysning, lysbjelker, bilpleie og tilbehør til bil, "
    "tilhenger og landbruksmaskiner) med å lage kunnskapsartikler fra løste kundehenvendelser. "
    "Du får en anonymisert e-posttråd med kundens henvendelse og kundeservicens svar. "
    "Skriv på norsk bokmål. Formuler problem og løsning generelt, slik at de gjelder alle kunder med samme "
    "problem: ikke nevn kunden, ordrer, datoer eller plassholdere som [NAVN]. "
    "Løsningen skal bare bygge på det kundeservice faktisk skrev; ikke legg til egne råd."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "sku": {"type": "string", "description": "SKU fra produktlisten, eller tom streng hvis ingen passer"},
        "title": {"type": "string", "description": "Kort tittel, maks 80 tegn"},
        "problem": {"type": "string", "description": "Problemet i én kort setning"},
        "solution": {"type": "string", "description": "Løsningen, 1–4 setninger"},
        "tags": {"type": "string", "description": "2–5 stikkord med små bokstaver, kommaseparert"},
    },
    "required": ["sku", "title", "problem", "solution", "tags"],
    "additionalProperties": False,
}


def extract_with_ai(text: str, products: list) -> Extraction:
    product_list = "\n".join(f"- {p.sku}: {p.name} ({p.category})" for p in products)
    prompt = (
        f"Produktliste (bruk bare disse SKU-ene):\n{product_list}\n\n"
        f"E-posttråd:\n<epost>\n{text}\n</epost>\n\n"
        "Trekk ut produkt, problem og løsning."
    )
    data = llm.complete_json(_AI_SYSTEM, prompt, _SCHEMA)

    # Produktet må være et av SKU-ene vi har; ellers null.
    known = {p.sku for p in products}
    sku = (data.get("sku") or "").strip().upper()
    return Extraction(
        sku=sku if sku in known else None,
        title=data.get("title", ""),
        problem=data.get("problem", ""),
        solution=data.get("solution", ""),
        tags=data.get("tags", ""),
        mode="KI",
    )


# ------------------------------------------------------------- Regelmodus

def extract_with_rules(text: str, products: list) -> Extraction:
    subject, support_part, customer_part = split_thread(text)

    product = find_product(text, products)
    problem = _first_paragraph(customer_part) or "Problemet kunne ikke leses ut automatisk – fyll inn manuelt."
    solution = _body_paragraphs(support_part) or "Løsningen kunne ikke leses ut automatisk – fyll inn manuelt."
    title = _clean_placeholders(SUBJECT_PREFIX.sub("", subject)) or problem[:80]

    tags = []
    if product:
        tags.append(product.category.lower())
    lowered = text.lower()
    for stem, tag in KEYWORD_TAGS.items():
        if stem in lowered and tag not in tags:
            tags.append(tag)

    return Extraction(
        sku=product.sku if product else None,
        title=title,
        problem=problem,
        solution=solution,
        tags=", ".join(tags[:5]),
        mode="regel",
    )


def split_thread(text: str) -> tuple[str, str, str]:
    """Deler tråden i (emne, kundeservicens svar, kundens henvendelse)."""
    subject = ""
    lines = text.splitlines()
    if lines and lines[0].lower().startswith("emne:"):
        subject = lines[0][5:].strip()
        text = "\n".join(lines[1:])
    if THREAD_SEPARATOR in text:
        support, customer = text.split(THREAD_SEPARATOR, 1)
    else:
        # Uten skillelinje vet vi ikke hvem som skrev hva; alt regnes som kundens tekst.
        support, customer = "", text
    return subject, support.strip(), customer.strip()


def find_product(text: str, products: list):
    """Finner produktet via SKU, og ellers via fullt produktnavn (lengste navn først)."""
    by_sku = {p.sku: p for p in products}
    for sku in SKU_PATTERN.findall(text):
        if sku in by_sku:
            return by_sku[sku]
    lowered = text.lower()
    for p in sorted(products, key=lambda p: len(p.name), reverse=True):
        if p.name.lower() in lowered:
            return p
    return None


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _general_sentences(paragraph: str) -> str:
    """Fjerner setninger som handler om den enkelte kunden (inneholder plassholdere),
    og høflighetsfraser som «Takk for henvendelsen»."""
    sentences = re.split(r"(?<=[.!?])\s+", paragraph.replace("\n", " "))
    kept = [
        s for s in sentences
        if s and not PLACEHOLDER.search(s) and not s.lower().startswith(("takk for", "ring meg"))
    ]
    return " ".join(kept).strip()


def _first_paragraph(customer: str) -> str:
    """Kundens første avsnitt med innhold (hopper over «Hei!»-linjer)."""
    for paragraph in _paragraphs(customer):
        if GREETING.match(paragraph) and len(paragraph.split()) <= 3:
            continue
        if SIGN_OFF.match(paragraph):
            break
        general = _general_sentences(paragraph)
        if general:
            return general
    return ""


def _body_paragraphs(support: str) -> str:
    """Kundeservicens svar uten hilsen, høflighetsfraser og signatur."""
    kept = []
    for paragraph in _paragraphs(support):
        if SIGN_OFF.match(paragraph):
            break  # alt etter «Mvh» er signatur
        if GREETING.match(paragraph) and len(paragraph.split()) <= 3:
            continue
        general = _general_sentences(paragraph)
        if general:
            kept.append(general)
    return "\n\n".join(kept)


def _clean_placeholders(text: str) -> str:
    text = PLACEHOLDER.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip(" -:,")


# ----------------------------------------------------------- Felles vask

def sanitize(result: Extraction) -> Extraction:
    """Siste sikkerhetsnett: kjør anonymiseringsreglene på alt som skal lagres,
    og kutt feltene til lengdene databasen tillater."""
    result.title = _clean_placeholders(anonymize_rules(result.title)[0])[:200] or "Uten tittel"
    result.problem = anonymize_rules(result.problem)[0].strip()
    result.solution = anonymize_rules(result.solution)[0].strip()
    result.tags = anonymize_rules(result.tags)[0].strip()[:300]
    return result
