"""Chatbot-demo for kunder: svarer bare ut fra godkjente artikler.

1. Finn de 3 mest relevante artiklene (samme søk som kunnskapsbasen).
   Artikler som er merket som utdatert, brukes aldri.
2. KI-modus: modellen svarer kun ut fra artiklene og sier fra hvis svaret ikke finnes.
   Den oppgir hvilke artikler den brukte, så bare de vises som kilde.
   Regelmodus: returner løsningen fra beste artikkel.
Svaret viser alltid hvilke artikler det bygger på, og når de ble godkjent. Navnet på
den ansatte som godkjente vises ikke til kunden, bare at kundesenteret har godkjent.
"""

import logging
import re

from sqlalchemy.orm import Session

from . import config, llm
from .search import search_articles

log = logging.getLogger(__name__)

# Vanlige ord som ikke sier noe om problemet. Fjernes før søk, ellers
# «drukner» stikkordene i spørsmålet.
STOPWORDS = {
    "hei", "hvorfor", "hvordan", "hva", "hvor", "hvilken", "hvilke", "når", "kan", "jeg", "du", "dere",
    "min", "mitt", "mine", "meg", "det", "den", "de", "er", "har", "og", "i", "på", "til", "med", "som",
    "en", "et", "ei", "at", "av", "for", "om", "fra", "skal", "bør", "må", "gjør", "gjøre", "noen",
    "ikke", "så", "etter", "blir", "ble", "vil", "da", "seg", "sin", "sitt", "sine", "hjelp", "takk",
}

NO_ANSWER = "Jeg fant dessverre ingen artikkel i kunnskapsdatabasen som svarer på dette. Kontakt kundeservice, så hjelper vi deg."

_AI_SYSTEM = (
    "Du er kundeservice-chatboten til JDD Utstyr. Svar kort og vennlig på norsk bokmål. "
    "Du skal BARE bruke informasjonen i artiklene du får oppgitt. Hvis artiklene ikke svarer på spørsmålet, "
    "si at du ikke fant svaret og anbefal kunden å kontakte kundeservice. Ikke finn på noe. "
    "Oppgi ID-ene til artiklene svaret bygger på i article_ids. Svarer ingen av artiklene på spørsmålet, "
    "sett found til false og article_ids til en tom liste."
)

# Svaret fra modellen må følge dette skjemaet (structured outputs).
_AI_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "description": "Svaret til kunden"},
        "found": {"type": "boolean", "description": "true hvis artiklene svarer på spørsmålet"},
        "article_ids": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "ID-ene til artiklene svaret bygger på",
        },
    },
    "required": ["answer", "found", "article_ids"],
    "additionalProperties": False,
}


def search_terms(question: str) -> str:
    words = re.findall(r"[\wæøåÆØÅ-]+", question.lower())
    kept = [w for w in words if w not in STOPWORDS and len(w) > 1]
    return " ".join(kept) or question


def answer(db: Session, question: str) -> dict:
    articles = search_articles(db, search_terms(question), limit=3, only_active=True)
    sources = [
        {
            "id": a["id"],
            "title": a["title"],
            "product_name": a["product_name"],
            "version": a["version"],
            "score": a["score"],
            # Kilde og dato, så kunden ser hvor ferskt svaret er. Den ansattes navn sendes
            # ikke til kunden; det ser de ansatte i Kunnskapsbase.
            "updated_at": a["updated_at"],
            "source_count": len(a["sources"]),
        }
        for a in articles
    ]

    if not articles:
        return {"answer": NO_ANSWER, "sources": [], "mode": "KI" if config.ai_enabled() else "regel"}

    if config.ai_enabled():
        try:
            context = "\n\n".join(
                f"<artikkel id=\"{a['id']}\">\nTittel: {a['title']}\nProdukt: {a['product_name'] or 'ukjent'}\n"
                f"Problem: {a['problem']}\nLøsning: {a['solution']}\n</artikkel>"
                for a in articles
            )
            prompt = f"Artikler fra kunnskapsdatabasen:\n{context}\n\nKundens spørsmål: {question}"
            data = llm.complete_json(_AI_SYSTEM, prompt, _AI_SCHEMA, max_tokens=4000)
            # Vis bare artiklene modellen faktisk brukte. Fant den ikke svaret, vises ingen kilder.
            used = set(data.get("article_ids") or [])
            shown = ([s for s in sources if s["id"] in used] or sources) if data.get("found") else []
            return {"answer": data["answer"], "sources": shown, "mode": "KI"}
        except Exception as exc:  # noqa: BLE001 – faller tilbake til regelmodus
            log.warning("KI-chat feilet, bruker regelmodus: %s", exc)

    best = articles[0]
    text = f"{best['title']}\n\n{best['solution']}"
    return {"answer": text, "sources": sources[:1], "mode": "regel"}
