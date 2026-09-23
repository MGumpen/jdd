"""Ingest-worker: henter nye e-poster fra Mailpit og kjører pipelinen.

Kjører i en egen tråd i API-containeren. Hvert POLL_INTERVAL_SECONDS:
  1. Innhenting     – hent nye meldinger fra Mailpit (hopp over kjente ID-er)
  2. Anonymisering  – før noe lagres
  3. Uttrekk        – produkt, problem, løsning (KI eller regler)
  4. Duplikatsjekk  – pg_trgm mot artikler for samme produkt
  5. Forslag        – lagres som «pending»; ingenting går til kunnskapsdatabasen uten godkjenning
Hvert steg skriver en rad i pipeline_events.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
from sqlalchemy import select

from . import config
from .anonymize import anonymize
from .db import SessionLocal
from .duplicates import check as check_duplicates
from .extract import extract
from .models import EmailItem, Product, Proposal, log_event

log = logging.getLogger(__name__)

# Flere e-poster behandles samtidig, slik at et tregt KI-kall ikke holder igjen resten.
PARALLEL_MESSAGES = 4

# Meldinger som feilet før de ble lagret (f.eks. nettverksfeil mot Mailpit).
# Huskes i minnet så vi ikke logger samme feil hvert tiende sekund.
_failed_before_storage: set[str] = set()


# ------------------------------------------------------------ Mailpit

def list_message_ids() -> list[str]:
    """ID-ene til alle meldinger i Mailpit, eldste først."""
    response = httpx.get(f"{config.MAILPIT_URL}/api/v1/messages", params={"limit": 500}, timeout=10)
    response.raise_for_status()
    messages = response.json().get("messages") or []
    return [m["ID"] for m in reversed(messages)]  # Mailpit gir nyeste først


def fetch_message(msg_id: str) -> dict:
    response = httpx.get(f"{config.MAILPIT_URL}/api/v1/message/{msg_id}", timeout=10)
    response.raise_for_status()
    return response.json()


def delete_all_messages() -> None:
    """Tømmer innboksen i Mailpit (brukes av /api/demo/reset)."""
    httpx.delete(f"{config.MAILPIT_URL}/api/v1/messages", timeout=10).raise_for_status()


# ----------------------------------------------------------- Pipeline

def poll_once() -> int:
    """Ett gjennomløp. Returnerer antall nye meldinger som ble behandlet."""
    with SessionLocal() as db:
        known = set(db.scalars(select(EmailItem.source_msg_id)))
    new_ids = [i for i in list_message_ids() if i not in known and i not in _failed_before_storage]
    if new_ids:
        log.info("Fant %d nye meldinger i Mailpit", len(new_ids))
        with ThreadPoolExecutor(max_workers=PARALLEL_MESSAGES) as pool:
            list(pool.map(process_message, new_ids))
    return len(new_ids)


def process_message(msg_id: str) -> None:
    # Steg 1 og 2: hent og anonymiser. Rå tekst finnes bare i minnet her.
    try:
        message = fetch_message(msg_id)
        raw_text = f"Emne: {message.get('Subject', '')}\n\n{message.get('Text', '')}"
        clean_text, anonymize_detail = anonymize(raw_text)
        del message, raw_text  # den rå teksten skal ikke brukes videre
    except Exception as exc:  # noqa: BLE001
        log.exception("Kunne ikke hente/anonymisere melding %s", msg_id)
        _failed_before_storage.add(msg_id)
        with SessionLocal() as db:
            log_event(db, None, "failed", f"Kunne ikke hente eller anonymisere melding: {type(exc).__name__}")
            db.commit()
        return

    with SessionLocal() as db:
        item = EmailItem(source_msg_id=msg_id, anonymized_text=clean_text, status="anonymized")
        db.add(item)
        db.flush()  # gir oss item.id
        log_event(db, item.id, "received", "Ny e-post hentet fra innboksen (Mailpit).")
        log_event(db, item.id, "anonymized", anonymize_detail)
        db.commit()

        step = "extracted"
        try:
            # Steg 3: uttrekk
            products = list(db.scalars(select(Product)))
            result = extract(clean_text, products)
            product = next((p for p in products if p.sku == result.sku), None)
            item.status = "extracted"
            log_event(
                db, item.id, "extracted",
                f"{'KI-modus' if result.mode == 'KI' else 'Regelmodus'}. Produkt: {f'{product.name} ({product.sku})' if product else 'ikke gjenkjent'}. "
                f"Tittel: «{result.title}». {result.note}".strip(),
            )
            db.commit()

            # Steg 4: duplikatsjekk
            step = "duplicate_check"
            dup = check_duplicates(db, result.problem, product.id if product else None)
            if dup.proposal_type == "update":
                dup_detail = (
                    f"Ligner artikkel #{dup.matched_article_id} ({dup.similarity:.0%} likhet ≥ "
                    f"{config.DUPLICATE_THRESHOLD:.0%}). Foreslår oppdatering."
                )
            elif dup.similarity is not None:
                dup_detail = (
                    f"Nærmeste artikkel #{dup.best_article_id} har {dup.similarity:.0%} likhet "
                    f"(under {config.DUPLICATE_THRESHOLD:.0%}). Foreslår ny artikkel."
                )
            else:
                dup_detail = "Ingen artikler å sammenligne med for dette produktet. Foreslår ny artikkel."
            log_event(db, item.id, "duplicate_check", dup_detail)

            # Steg 5: forslag
            step = "proposed"
            proposal = Proposal(
                email_item_id=item.id,
                product_id=product.id if product else None,
                title=result.title,
                problem=result.problem,
                solution=result.solution,
                tags=result.tags,
                proposal_type=dup.proposal_type,
                matched_article_id=dup.matched_article_id,
                similarity=dup.similarity,
                status="pending",
            )
            db.add(proposal)
            db.flush()
            item.status = "proposed"
            kind = "oppdatering" if dup.proposal_type == "update" else "ny artikkel"
            log_event(db, item.id, "proposed", f"Forslag #{proposal.id} ({kind}) venter på godkjenning.")
            db.commit()
        except Exception as exc:  # noqa: BLE001 – feil i én e-post skal ikke stoppe resten
            log.exception("Pipeline feilet for e-post %s i steg %s", item.id, step)
            db.rollback()
            item.status = "failed"
            log_event(db, item.id, "failed", f"Feil i steg «{step}»: {type(exc).__name__}: {exc}"[:1000])
            db.commit()


# -------------------------------------------------------------- Løkke

def run_forever(stop: threading.Event) -> None:
    mode = f"KI-modus ({config.LLM_MODEL})" if config.ai_enabled() else "regelmodus"
    log.info("Worker startet i %s, poller Mailpit hvert %ss", mode, config.POLL_INTERVAL_SECONDS)
    while not stop.is_set():
        try:
            poll_once()
        except Exception:  # noqa: BLE001 – f.eks. Mailpit eller databasen er ikke klar ennå
            log.exception("Polling feilet, prøver igjen om %ss", config.POLL_INTERVAL_SECONDS)
        stop.wait(config.POLL_INTERVAL_SECONDS)


def start_in_background() -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=run_forever, args=(stop,), name="ingest-worker", daemon=True).start()
    return stop
