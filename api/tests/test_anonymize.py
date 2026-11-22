"""Tester for anonymiseringen.

Akseptansekriterium: ingen navn, e-postadresser, telefonnumre eller ordrenumre
fra testdataene skal finnes i databasen.
"""

import os
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from app.anonymize import anonymize_rules
from app.extract import extract_with_rules, sanitize

from .conftest import find_pii, load_seed_emails, thread_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Send til kari.berg@example.com i dag", "Send til [E-POST] i dag"),
        ("Ring 912 34 567", "Ring [TELEFON]"),
        ("Ring +47 48 12 34 56", "Ring [TELEFON]"),
        ("Tlf 40011223.", "Tlf [TELEFON]."),
        ("Mobil: +4799887766", "Mobil: [TELEFON]"),
        ("Gjelder ORD-58213", "Gjelder [ORDRE]"),
        ("på ordre nr 104577.", "på [ORDRE]."),
        ("Ordrenummer: 998812", "[ORDRE]"),
        ("Storgata 12", "[ADRESSE]"),
        ("Bjørkeveien 7, 3120 Nøtterøy", "[ADRESSE], [ADRESSE]"),
        ("Hei Kari,", "Hei [NAVN],"),
        ("Mvh\nKari Berg\nJDD Utstyr", "Mvh\n[NAVN]\nJDD Utstyr"),
        ("Mvh Sindre Haugland", "Mvh [NAVN]"),
        ("Med vennlig hilsen\nMarte Aas", "Med vennlig hilsen\n[NAVN]"),
        ("Mvh\r\nKari Berg\r\n", "Mvh\n[NAVN]\n"),  # linjeskift fra SMTP
        ("Jeg heter Ola Nordmann og", "Jeg heter [NAVN] og"),
        # Postnummer og poststed etter en gateadresse, eller alene på en linje
        ("Storgata 12\n4612 Kristiansand", "[ADRESSE]\n[ADRESSE]"),
        ("Mvh\nKari\n4612 Kristiansand\n", "Mvh\n[NAVN]\n[ADRESSE]\n"),
        # Registreringsnummer, fødselsnummer og kontonummer
        ("Bilen har regnr. AB 12345.", "Bilen har regnr. [REGNR]."),
        ("Skiltet er EL12345", "Skiltet er [REGNR]"),
        ("Fødselsnummer 01017012345", "Fødselsnummer [FØDSELSNR]"),
        ("Kontonummer 1234.56.78901", "Kontonummer [KONTONR]"),
        # Små bokstaver i hilsen og signatur, og flere navn i hilsenen
        ("hei Kari,", "hei [NAVN],"),
        ("mvh\nola nordmann", "mvh\n[NAVN]"),
        ("Hei Kari Berg og Per,", "Hei [NAVN],"),
    ],
)
def test_rules(raw, expected):
    assert anonymize_rules(raw)[0] == expected


@pytest.mark.parametrize(
    "text",
    [
        "Hei!\nLysbjelken flimrer.",           # «Lysbjelken» er ikke et navn
        "Hei,\nJeg har kjøpt DEMO-001.",       # SKU skal stå urørt
        "To lysbjelker på 120 W og 25 A sikring på 12 V.",  # tekniske tall
        "Mvh\nJDD Utstyr kundeservice",        # firmanavn i signaturen
        # Teknisk informasjon som ligner postnummer eller telefonnummer, men er produktkunnskap
        "Passer modeller fra 2018 Toyota Hilux.",
        "Lysbjelken gir 9600 Lumen ved 12 V.",
        "Arbeidslyset er på 4800 Watt totalt.",
        "Varenummer 12345678 er utsolgt.",
        "Art.nr: 87654321 passer også.",
        # Ord som står der et navn ofte står, men ikke er navn
        "Hilsen Kundeservice",
        "Mvh\nkundeservice",
    ],
)
def test_rules_leave_normal_text_alone(text):
    assert anonymize_rules(text)[0] == text


def test_rule_counts_only_real_replacements():
    """Hendelsesloggen skal vise hvor mange opplysninger som faktisk ble fjernet."""
    _, counts = anonymize_rules("Ring 912 34 567. Varenummer 12345678.")
    assert counts == {"telefon": 1}


@pytest.mark.parametrize("email", load_seed_emails(), ids=lambda e: e["subject"])
def test_seed_email_is_fully_anonymized(email):
    """Hver oppdiktede e-post: ingen av personopplysningene skal overleve."""
    cleaned, _ = anonymize_rules(thread_text(email))
    assert find_pii(cleaned, email["test_pii"]) == []


@pytest.mark.parametrize("email", load_seed_emails(), ids=lambda e: e["subject"])
def test_rule_extraction_contains_no_pii(email):
    """Også forslaget som lages i regelmodus skal være fritt for personopplysninger."""
    cleaned, _ = anonymize_rules(thread_text(email))
    result = sanitize(extract_with_rules(cleaned, products=[]))
    for field in (result.title, result.problem, result.solution, result.tags):
        assert find_pii(field, email["test_pii"]) == []


def test_rule_extraction_finds_product_and_parts():
    products = [SimpleNamespace(sku="DEMO-001", name="LED-lysbjelke Nordlys 50 cm", category="Lysbjelke")]
    email = load_seed_emails()[0]  # lysbjelke som flimrer
    result = extract_with_rules(anonymize_rules(thread_text(email))[0], products)
    assert result.sku == "DEMO-001"
    assert result.problem == "Lysbjelken min flimrer når motoren startes."
    assert "relé" in result.solution
    assert "Mvh" not in result.solution and "[NAVN]" not in result.solution


def _database_texts() -> list[str] | None:
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT anonymized_text FROM email_items
                    UNION ALL SELECT concat_ws(' ', title, problem, solution, tags) FROM proposals
                    UNION ALL SELECT concat_ws(' ', title, problem, solution, tags) FROM knowledge_articles
                    UNION ALL SELECT concat_ws(' ', title, problem, solution, tags) FROM article_versions
                    UNION ALL SELECT coalesce(detail, '') FROM pipeline_events
                    """
                )
            ).scalars().all()
        engine.dispose()
        return rows
    except Exception:  # noqa: BLE001
        return None


def test_no_pii_in_database():
    """Integrasjonstest: kjøres mot databasen etter at seed-e-postene er behandlet."""
    texts = _database_texts()
    if not texts:
        pytest.skip("Ingen database tilgjengelig, eller ingen data ennå (kjør seed først)")
    all_pii = [value for email in load_seed_emails() for value in email["test_pii"]]
    everything = "\n".join(texts)
    assert find_pii(everything, all_pii) == []
