"""Steg 2: anonymisering. Kjøres FØR noe lagres i databasen.

Regelbasert vask med regulære uttrykk kjøres alltid. I KI-modus sendes den
allerede vaskede teksten i tillegg til modellen, som fjerner det reglene
ikke fanger (f.eks. et navn midt i en setning). Modellen ser aldri rå tekst.
"""

import logging
import re
from collections.abc import Callable

from . import config, llm

log = logging.getLogger(__name__)

# Store bokstaver i norske navn, og små bokstaver som kan følge.
_UP = "A-ZÆØÅ"
_LOW = "a-zæøåéü"
# Ord som står der et navn ofte står, men som ikke er navn ("Hilsen Kundeservice", "Mvh Teamet").
_NOT_NAME = rf"(?!(?i:kundeservice|kundesenter|kundeavdeling|support|team|alle|oss|jdd)[{_LOW}]*\b)"
# Ett til tre navneledd på samme linje, f.eks. "Kari", "Kari Berg", "Anne-Lise Berg".
_NAME = rf"{_NOT_NAME}[{_UP}][{_LOW}]+(?:[ \t-]+[{_UP}][{_LOW}]+){{0,2}}"
# Samme, men med små bokstaver ("ola nordmann"). Brukes bare når navnet står alene på en linje.
_NAME_LOWER = rf"{_NOT_NAME}[{_LOW}]+(?:[ \t-]+[{_LOW}]+){{0,2}}"
# Hilsener og avslutninger, uavhengig av store/små bokstaver ("Mvh", "mvh", "MVH").
_GREETING = r"(?i:hei|hallo|heisann|kjære)"
_SIGN_OFF = r"(?i:mvh\.?|med vennlig hilsen|vennlig hilsen|beste hilsen|hilsen)"
# Poststed etter postnummer, f.eks. "Kristiansand" eller "Mo-Rana".
_PLACE = rf"[{_UP}][{_LOW}]+(?:[ -][{_UP}][{_LOW}]+)?"

# Ord som betyr at tallet etter er et varenummer, ikke et telefonnummer ("Varenummer 12345678").
_PRODUCT_NUMBER_BEFORE = re.compile(
    r"(?:varenummer|varenr|artikkelnummer|artikkelnr|art\.?[ \t]?nr|delenummer|delenr|produktnummer|ean)"
    r"\.?[ \t]*:?[ \t]*$",
    re.IGNORECASE,
)


def _phone(match: re.Match) -> str:
    """Erstatter et telefonnummer, men lar varenumre stå (de er nyttig produktkunnskap)."""
    before = match.string[max(0, match.start() - 25):match.start()]
    if _PRODUCT_NUMBER_BEFORE.search(before):
        return match.group(0)
    return "[TELEFON]"


# Hver regel: (navn til hendelsesloggen, mønster, erstatning). Erstatningen er enten en tekst
# som "[E-POST]", eller en funksjon som bestemmer selv (og kan la treffet stå).
#
# Rekkefølgen betyr noe:
# - ordrenumre, fødselsnumre og kontonumre fjernes før telefonnumre, ellers kan de bli
#   tolket som telefonnumre,
# - gateadresser fjernes før postnummer, fordi postnummer-regelen ser etter [ADRESSE] foran.
RULES: list[tuple[str, re.Pattern, str | Callable[[re.Match], str]]] = [
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
        "fødselsnummer",
        # 11 siffer, f.eks. "01017012345" eller "010170 12345".
        re.compile(r"(?<!\d)\d{6}[ -]?\d{5}(?!\d)"),
        "[FØDSELSNR]",
    ),
    (
        "kontonummer",
        # Bankkontonummer med punktum eller mellomrom, f.eks. "1234.56.78901".
        re.compile(r"(?<!\d)\d{4}[ .]\d{2}[ .]\d{5}(?!\d)"),
        "[KONTONR]",
    ),
    (
        "telefon",
        # 8 siffer, gjerne med mellomrom/bindestrek, eventuelt med +47 eller 0047 foran.
        # Står det «varenummer» o.l. rett foran, er det ikke et telefonnummer (se _phone).
        re.compile(r"(?<!\w)(?:(?:\+|00)47[ -]?)?(?:\d[ -]?){7}\d(?!\d)"),
        _phone,
    ),
    (
        "registreringsnummer",
        # Norske bilskilt: to store bokstaver og fem siffer, f.eks. "AB 12345" eller "EL12345".
        re.compile(rf"\b[{_UP}]{{2}}[ -]?\d{{5}}\b"),
        "[REGNR]",
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
        # Postnummer og poststed rett etter en gateadresse: "[ADRESSE], 3120 Nøtterøy" eller på linjen under.
        # Fire siffer og et ord er ellers ofte teknisk informasjon («2018 Toyota Hilux», «9600 Lumen»),
        # så vi fjerner dem bare når de står i en adresse.
        re.compile(rf"(\[ADRESSE\][ \t]*(?:,[ \t]*|\n[ \t]*))\d{{4}}[ \t]+{_PLACE}"),
        r"\1[ADRESSE]",
    ),
    (
        "postnummer",
        # Postnummer og poststed alene på en linje, f.eks. i en signatur: "4612 Kristiansand".
        re.compile(rf"(?m)^([ \t]*)\d{{4}}[ \t]+{_PLACE}[ \t]*$"),
        r"\1[ADRESSE]",
    ),
    (
        "navn i hilsen",
        # "Hei Kari," / "hallo Kari Berg!" / "Hei Kari og Per" – navnene må stå på samme linje som hilsenen.
        re.compile(rf"\b({_GREETING})([ \t]+){_NAME}(?:[ \t]+(?:og|&)[ \t]+{_NAME})*"),
        r"\1\2[NAVN]",
    ),
    (
        "navn i signatur",
        # "Mvh Kari Berg" eller "Mvh" med navnet på linjen under.
        re.compile(rf"\b({_SIGN_OFF})([ \t]*,?[ \t]*\n?[ \t]*){_NAME}"),
        r"\1\2[NAVN]",
    ),
    (
        "navn i signatur",
        # Navn med små bokstaver alene på linjen under avslutningen: "mvh\nola nordmann".
        re.compile(rf"(?m)\b({_SIGN_OFF})([ \t]*,?[ \t]*\n[ \t]*){_NAME_LOWER}[ \t]*$"),
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
    """Regelbasert vask. Returnerer vasket tekst og antall erstatninger per regel."""
    # E-post over SMTP har Windows-linjeskift (\r\n). Reglene forventer \n.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    counts: dict[str, int] = {}
    for name, pattern, replacement in RULES:

        def replace(match: re.Match, replacement=replacement, name=name) -> str:
            new = replacement(match) if callable(replacement) else match.expand(replacement)
            if new != match.group(0):  # tell bare treff som faktisk ble erstattet
                counts[name] = counts.get(name, 0) + 1
            return new

        text = pattern.sub(replace, text)
    return text, counts


_AI_SYSTEM = (
    "Du fjerner personopplysninger fra norske kundeservice-e-poster. "
    "Teksten er allerede delvis vasket; plassholdere som [NAVN], [E-POST], [TELEFON], [ORDRE], [ADRESSE], "
    "[REGNR], [FØDSELSNR] og [KONTONR] skal stå urørt. "
    "Erstatt gjenværende personnavn med [NAVN], adresser med [ADRESSE], telefonnumre med [TELEFON], "
    "e-postadresser med [E-POST], ordrenumre med [ORDRE], bilens registreringsnummer med [REGNR], "
    "fødselsnumre med [FØDSELSNR] og kontonumre med [KONTONR]. "
    "Ikke endre noe annet: behold produktnavn, varenumre (f.eks. DEMO-001), firmanavn, bilmerke, årsmodell "
    "og all teknisk informasjon ordrett. "
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
