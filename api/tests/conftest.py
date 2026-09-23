"""Felles hjelpere for testene."""

import json
import os
import re
from pathlib import Path

import pytest

from app.extract import THREAD_SEPARATOR

# I Docker monteres seed/emails inn på /seed/emails (se docker-compose.yml).
# Lokalt ligger mappen to nivåer opp fra api/tests.
_CANDIDATES = [
    os.getenv("SEED_EMAILS_DIR", ""),
    "/seed/emails",
    str(Path(__file__).resolve().parents[2] / "seed" / "emails"),
]


def seed_emails_dir() -> Path:
    for candidate in _CANDIDATES:
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    pytest.skip("Fant ikke seed/emails")


def load_seed_emails() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(seed_emails_dir().glob("*.json"))]


def thread_text(email: dict) -> str:
    """Bygger teksten slik workeren ser den: emnelinje + e-postkropp.
    Må holdes lik build_body() i seed/send_emails.py."""
    body = f"{email['support_reply']}\n\n{THREAD_SEPARATOR}\n{email['customer_message']}\n"
    text = f"Emne: SV: {email['subject']}\n\n{body}"
    # Mailpit leverer teksten med Windows-linjeskift, slik e-post over SMTP har.
    return text.replace("\n", "\r\n")


def find_pii(text: str, pii: list[str]) -> list[str]:
    """Returnerer personopplysningene fra testdataene som fortsatt finnes i teksten.
    Ordgrenser brukes, så «Berg» ikke gir treff i «Bergen»."""
    return [value for value in pii if re.search(rf"(?<!\w){re.escape(value)}(?!\w)", text)]
