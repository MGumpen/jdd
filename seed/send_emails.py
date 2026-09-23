"""Sender de oppdiktede e-posttrådene i emails/ til Mailpit via SMTP.

Mailpit etterligner kundesenterets innboks. Hver e-post er et svar fra
kundeservice med kundens opprinnelige henvendelse sitert under, slik
en vanlig e-posttråd ser ut.

Kjøres med:  docker compose run --rm seed
"""

import json
import os
import smtplib
import sys
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", "1025"))
DELAY_SECONDS = float(os.getenv("SEED_DELAY_SECONDS", "2"))
SUPPORT_ADDRESS = "Kundeservice JDD Utstyr (demo) <kundeservice@jdd-demo.example>"

# Må være lik THREAD_SEPARATOR i api/app/extract.py.
THREAD_SEPARATOR = "----- Opprinnelig melding -----"


def build_body(email: dict) -> str:
    return f"{email['support_reply']}\n\n{THREAD_SEPARATOR}\n{email['customer_message']}\n"


def build_message(email: dict) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = SUPPORT_ADDRESS
    msg["To"] = email["from"]
    msg["Subject"] = f"SV: {email['subject']}"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="jdd-demo.example")
    msg.set_content(build_body(email))
    return msg


def connect(retries: int = 10) -> smtplib.SMTP:
    """Mailpit kan bruke et par sekunder på å starte; prøv noen ganger."""
    for attempt in range(1, retries + 1):
        try:
            return smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
        except OSError as exc:
            print(f"Venter på Mailpit ({attempt}/{retries}): {exc}")
            time.sleep(2)
    sys.exit("Fikk ikke kontakt med Mailpit.")


def main() -> None:
    files = sorted((Path(__file__).parent / "emails").glob("*.json"))
    print(f"Sender {len(files)} oppdiktede e-poster til {SMTP_HOST}:{SMTP_PORT}, én hvert {DELAY_SECONDS:g}. sekund")
    for i, path in enumerate(files, start=1):
        email = json.loads(path.read_text(encoding="utf-8"))
        with connect() as smtp:
            smtp.send_message(build_message(email))
        print(f"  [{i}/{len(files)}] {email['subject']}")
        if i < len(files):
            time.sleep(DELAY_SECONDS)  # slik at dataflyten kan følges live
    print("Ferdig. Følg med i Dataflyt-fanen på http://localhost:8080")


if __name__ == "__main__":
    main()
