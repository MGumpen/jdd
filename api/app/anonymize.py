"""Steg 2: anonymisering. Kjøres FØR noe lagres i databasen.

Regelbasert vask med regulære uttrykk kjøres alltid. I KI-modus sendes den
allerede vaskede teksten i tillegg til modellen, som fjerner det reglene
ikke fanger (f.eks. et navn midt i en setning). Modellen ser aldri rå tekst.
"""

import logging
import re

from . import config, llm

log = logging.getLogger(__name__)

# Store bokstaver i norske navn, og små bokstaver som kan følge.
_UP = "A-ZÆØÅ"
_LOW = "a-zæøåéü"
# Ett til tre navneledd på samme linje, f.eks. "Kari", "Kari Berg", "Anne-Lise Berg".
_NAME = rf"[{_UP}][{_LOW}]+(?:[ \t-]+[{_UP}][{_LOW}]+){{0,2}}"

# Rekkefølgen betyr noe: ordrenumre fjernes før telefonnumre, ellers kan
# lange ordrenumre bli tolket som telefonnumre.
RULES: list[tuple[str, re.Pattern, str]] = [
    (
        "e-post",
        re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
        "[E-POST]",
    ),
    (
        "ordrenummer",
        # "ORD-58213", "Ordre nr. 104577", "ordrenr 104577", "Ordrenummer: 998812"
        re.compile(r"\bORD-?\d{3,}\b|\b(?:ordre\s*(?:nr|nummer)\.?\s*:?\s*#?\s*)\d{3,}\b", re.IGNORECASE),
        "[ORDRE]",
    ),
    (
        "telefon",
        # 8 siffer, gjerne med mellomrom/bindestrek, eventuelt med +47 eller 0047 foran.
        re.compile(r"(?<!\w)(?:(?:\+|00)47[ -]?)?(?:\d[ -]?){7}\d(?!\d)"),
        "[TELEFON]",
    ),
    (
        "adresse",
        # Gateadresser som "Storgata 12" eller "Bjørkeveien 7B".
        re.compile(
            rf"\b[{_UP}][{_LOW}]*(?:gata|gaten|gate|veien|vegen|vei|veg|stien|bakken|plassen|allé|alle)"
            r"[ \t]+\d+ ?[A-Za-z]?\b"
        ),
        "[ADRESSE]",
    ),
    (
        "postnummer",
        # Postnummer og poststed, f.eks. "4612 Kristiansand".
        re.compile(rf"\b\d{{4}}[ \t]+[{_UP}][{_LOW}]+(?:[ -][{_UP}][{_LOW}]+)?"),
        "[ADRESSE]",
    ),
    (
        "navn i hilsen",
        # "Hei Kari," / "Hallo Kari Berg!" – navnet må stå på samme linje som hilsenen.
        re.compile(rf"\b(Hei|Hallo|Heisann|Kjære)([ \t]+){_NAME}"),
        r"\1\2[NAVN]",
    ),
    (
        "navn i signatur",
        # "Mvh Kari Berg" eller "Mvh" med navnet på linjen under.
        re.compile(
            rf"\b(Mvh\.?|Med vennlig hilsen|Vennlig hilsen|Beste hilsen|Hilsen)([ \t]*,?[ \t]*\n?[ \t]*){_NAME}"
        ),
        r"\1\2[NAVN]",
    ),
    (
        "navn i tekst",
        # "Jeg heter Kari Berg" / "mitt navn er Kari"
        re.compile(rf"\b([Jj]eg heter|[Mm]itt navn er)([ \t]+){_NAME}"),
        r"\1\2[NAVN]",
    ),
]


def anonymize_rules(text: str) -> tuple[str, dict[str, int]]:
    """Regelbasert vask. Returnerer vasket tekst og antall treff per regel."""
    # E-post over SMTP har Windows-linjeskift (\r\n). Reglene forventer \n.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    counts: dict[str, int] = {}
    for name, pattern, replacement in RULES:
        text, n = pattern.subn(replacement, text)
        if n:
            counts[name] = counts.get(name, 0) + n
    return text, counts


_AI_SYSTEM = (
    "Du fjerner personopplysninger fra norske kundeservice-e-poster. "
    "Teksten er allerede delvis vasket; plassholdere som [NAVN], [E-POST], [TELEFON], [ORDRE] og [ADRESSE] skal stå urørt. "
    "Erstatt gjenværende personnavn med [NAVN], adresser med [ADRESSE], telefonnumre med [TELEFON], "
    "e-postadresser med [E-POST], ordrenumre med [ORDRE] og bilens registreringsnummer med [REGNR]. "
    "Ikke endre noe annet: behold produktnavn, varenumre (f.eks. DEMO-001), firmanavn og all teknisk informasjon ordrett. "
    "Svar bare med den vaskede teksten, uten forklaring."
)


def anonymize(text: str) -> tuple[str, str]:
    """Full anonymisering. Returnerer (vasket tekst, forklaring til hendelsesloggen)."""
    cleaned, counts = anonymize_rules(text)
    summary = ", ".join(f"{name}: {n}" for name, n in counts.items()) or "ingen treff"
    detail = f"Regler erstattet {sum(counts.values())} opplysninger ({summary})."

    if config.ai_enabled():
        try:
            ai_text = llm.complete(_AI_SYSTEM, cleaned)
            # Kjør reglene én gang til på KI-svaret, i tilfelle modellen har gjort feil.
            cleaned, _ = anonymize_rules(ai_text)
            detail += " KI-gjennomgang fullført."
        except Exception as exc:  # noqa: BLE001 – vi faller alltid tilbake til regelvasket tekst
            log.warning("KI-anonymisering feilet: %s", exc)
            detail += f" KI-gjennomgang feilet ({type(exc).__name__}); bruker regelvasket tekst."

    return cleaned, detail
