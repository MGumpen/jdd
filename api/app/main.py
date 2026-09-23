"""FastAPI-app og ruter. Automatisk API-dokumentasjon finnes på /docs.

Ingen innlogging i prototypen: ansattnavnet sendes med i forespørselen.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from . import chat, config, review, worker
from .db import get_db
from .models import EmailItem, KnowledgeArticle, PipelineEvent, Product, Proposal
from .search import search_articles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bakgrunnsjobben (ingest-worker) kjører i samme container som API-et.
    stop = worker.start_in_background() if config.WORKER_ENABLED else None
    yield
    if stop:
        stop.set()


app = FastAPI(
    title="JDD kunnskapsdatabase (prototype)",
    description="Fra kundeservice-e-post til anonymisert, godkjent kunnskapsartikkel. All data er oppdiktet.",
    lifespan=lifespan,
)


# ------------------------------------------------------------- Hjelpere

def iso(value: datetime | None) -> str | None:
    """Tidspunkter lagres i UTC; «Z» forteller nettleseren det, så den viser lokal tid."""
    return value.isoformat(timespec="seconds") + "Z" if value else None


def product_json(p: Product | None) -> dict | None:
    return {"id": p.id, "sku": p.sku, "name": p.name, "category": p.category} if p else None


def article_json(a: KnowledgeArticle) -> dict:
    return {
        "id": a.id,
        "product": product_json(a.product),
        "title": a.title,
        "problem": a.problem,
        "solution": a.solution,
        "tags": a.tags,
        "version": a.version,
        "approved_by": a.approved_by,
        "approved_at": iso(a.approved_at),
        "updated_at": iso(a.updated_at),
    }


def proposal_json(p: Proposal) -> dict:
    return {
        "id": p.id,
        "email_item_id": p.email_item_id,
        "product": product_json(p.product),
        "title": p.title,
        "problem": p.problem,
        "solution": p.solution,
        "tags": p.tags,
        "proposal_type": p.proposal_type,
        "similarity": float(p.similarity) if p.similarity is not None else None,
        "matched_article": article_json(p.matched_article) if p.matched_article else None,
        "status": p.status,
        "reviewed_by": p.reviewed_by,
        "reviewed_at": iso(p.reviewed_at),
        "created_at": iso(p.created_at),
    }


def event_json(e: PipelineEvent) -> dict:
    return {
        "id": e.id,
        "email_item_id": e.email_item_id,
        "step": e.step,
        "detail": e.detail,
        "created_at": iso(e.created_at),
    }


# ----------------------------------------------------------- Forslag

class ApproveRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=100)
    title: str | None = Field(default=None, max_length=200)
    problem: str | None = None
    solution: str | None = None
    tags: str | None = Field(default=None, max_length=300)
    product_id: int | None = None  # valgfritt: den ansatte kan velge riktig produkt


class RejectRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=1000)


@app.get("/api/proposals")
def list_proposals(status: str | None = "pending", db: Session = Depends(get_db)):
    query = select(Proposal).order_by(Proposal.created_at, Proposal.id)
    if status:
        query = query.where(Proposal.status == status)
    return [proposal_json(p) for p in db.scalars(query)]


@app.get("/api/proposals/{proposal_id}")
def get_proposal(proposal_id: int, db: Session = Depends(get_db)):
    proposal = db.get(Proposal, proposal_id)
    if proposal is None:
        raise HTTPException(404, "Forslaget finnes ikke")
    events = db.scalars(
        select(PipelineEvent)
        .where(PipelineEvent.email_item_id == proposal.email_item_id)
        .order_by(PipelineEvent.id)
    )
    return {
        **proposal_json(proposal),
        "email_text": proposal.email_item.anonymized_text if proposal.email_item else None,
        "events": [event_json(e) for e in events],
    }


@app.post("/api/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: int, body: ApproveRequest, db: Session = Depends(get_db)):
    reviewer = body.reviewer.strip()
    if not reviewer:
        raise HTTPException(422, "Ansattnavn mangler")
    # Bare felt som faktisk ble sendt, tas med (slik at product_id=null kan bety «ingen produkt»).
    edits = body.model_dump(include=body.model_fields_set - {"reviewer"})
    if edits.get("product_id") is not None and db.get(Product, edits["product_id"]) is None:
        raise HTTPException(422, "Ukjent produkt")
    try:
        article = review.approve(db, proposal_id, reviewer, edits)
    except review.ReviewError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"status": "approved", "article": article_json(article)}


@app.post("/api/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int, body: RejectRequest, db: Session = Depends(get_db)):
    reviewer, reason = body.reviewer.strip(), body.reason.strip()
    if not reviewer or not reason:
        raise HTTPException(422, "Ansattnavn og begrunnelse må fylles ut")
    try:
        proposal = review.reject(db, proposal_id, reviewer, reason)
    except review.ReviewError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"status": "rejected", "proposal": proposal_json(proposal)}


# ----------------------------------------------------------- Artikler

@app.get("/api/articles")
def list_articles(
    q: str = "",
    product: int | None = Query(default=None, description="Produkt-ID"),
    db: Session = Depends(get_db),
):
    return search_articles(db, q, product)


@app.get("/api/articles/{article_id}")
def get_article(article_id: int, db: Session = Depends(get_db)):
    article = db.get(KnowledgeArticle, article_id)
    if article is None:
        raise HTTPException(404, "Artikkelen finnes ikke")
    return article_json(article)


@app.get("/api/products")
def list_products(db: Session = Depends(get_db)):
    return [product_json(p) for p in db.scalars(select(Product).order_by(Product.sku))]


# ------------------------------------------------------------- Chatbot

class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


@app.post("/api/chat")
def chat_endpoint(body: ChatRequest, db: Session = Depends(get_db)):
    return chat.answer(db, body.question.strip())


# ------------------------------------------------- Dataflyt og status

@app.get("/api/events")
def list_events(limit: int = Query(default=50, ge=1, le=500), db: Session = Depends(get_db)):
    events = db.scalars(select(PipelineEvent).order_by(PipelineEvent.id.desc()).limit(limit))
    return [event_json(e) for e in events]


@app.get("/api/stats")
def stats(db: Session = Depends(get_db)):
    proposals = dict(db.execute(select(Proposal.status, func.count()).group_by(Proposal.status)).all())
    emails = dict(db.execute(select(EmailItem.status, func.count()).group_by(EmailItem.status)).all())
    return {
        "emails": sum(emails.values()),
        "emails_failed": emails.get("failed", 0),
        "proposals": {s: proposals.get(s, 0) for s in ("pending", "approved", "rejected")},
        "articles": db.scalar(select(func.count()).select_from(KnowledgeArticle)),
        "mode": "KI" if config.ai_enabled() else "regel",
        "model": config.LLM_MODEL if config.ai_enabled() else None,
        "duplicate_threshold": config.DUPLICATE_THRESHOLD,
    }


@app.post("/api/demo/reset")
def demo_reset(db: Session = Depends(get_db)):
    """Tømmer alt unntatt produkter og seedartikler, slik at demoen kan kjøres på nytt."""
    db.execute(text("TRUNCATE pipeline_events, proposals, email_items, knowledge_articles RESTART IDENTITY"))
    db.execute(text("SELECT seed_demo_articles()"))  # definert i db/seed.sql
    db.commit()
    # Tøm også innboksen, ellers ville workeren behandlet de gamle e-postene på nytt.
    try:
        worker.delete_all_messages()
        inbox = "Innboksen i Mailpit er tømt."
    except Exception as exc:  # noqa: BLE001
        log.warning("Kunne ikke tømme Mailpit: %s", exc)
        inbox = "Klarte ikke å tømme Mailpit; gamle e-poster kan bli behandlet på nytt."
    return {"status": "ok", "message": f"Demoen er nullstilt. {inbox}"}


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}
