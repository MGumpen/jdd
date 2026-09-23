"""Godkjenning og avvisning av forslag. Det er BARE her artikler skrives.

- Godkjent «new»:    ny rad i knowledge_articles.
- Godkjent «update»: den matchede artikkelen oppdateres og får version + 1.
- Avvist:            forslaget merkes «rejected», begrunnelsen lagres i hendelsesloggen.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from .extract import Extraction, sanitize
from .models import KnowledgeArticle, Proposal, log_event


def utcnow() -> datetime:
    """Databasen lagrer tidspunkter i UTC uten tidssone (samme som now() i Postgres)."""
    return datetime.now(UTC).replace(tzinfo=None)


class ReviewError(Exception):
    """Forslaget kan ikke behandles (finnes ikke / allerede behandlet)."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _pending(db: Session, proposal_id: int) -> Proposal:
    # with_for_update låser raden, så to ansatte ikke kan godkjenne samme forslag samtidig.
    proposal = db.get(Proposal, proposal_id, with_for_update=True)
    if proposal is None:
        raise ReviewError("Forslaget finnes ikke", 404)
    if proposal.status != "pending":
        raise ReviewError(f"Forslaget er allerede behandlet ({proposal.status})", 409)
    return proposal


def approve(db: Session, proposal_id: int, reviewer: str, edits: dict) -> KnowledgeArticle:
    proposal = _pending(db, proposal_id)

    # Den ansatte kan ha redigert feltene. Tomme felt betyr «behold forslaget».
    for field in ("title", "problem", "solution", "tags"):
        value = edits.get(field)
        if value is not None and value.strip():
            setattr(proposal, field, value.strip())
    if "product_id" in edits:
        proposal.product_id = edits["product_id"]

    # Anonymiseringsreglene kjøres på nytt, i tilfelle den ansatte har limt inn personopplysninger.
    clean = sanitize(Extraction(
        sku=None, title=proposal.title, problem=proposal.problem,
        solution=proposal.solution, tags=proposal.tags or "", mode="manuell",
    ))
    proposal.title, proposal.problem, proposal.solution, proposal.tags = clean.title, clean.problem, clean.solution, clean.tags

    now = utcnow()
    article = db.get(KnowledgeArticle, proposal.matched_article_id) if proposal.proposal_type == "update" else None
    if article is not None:
        article.title = proposal.title
        article.problem = proposal.problem
        article.solution = proposal.solution
        article.tags = proposal.tags
        article.product_id = proposal.product_id
        article.version = article.version + 1
        article.approved_by = reviewer
        article.approved_at = now
        article.updated_at = now
        detail = f"Godkjent av {reviewer}. Artikkel #{article.id} oppdatert til versjon {article.version}."
    else:
        article = KnowledgeArticle(
            product_id=proposal.product_id,
            title=proposal.title,
            problem=proposal.problem,
            solution=proposal.solution,
            tags=proposal.tags,
            version=1,
            approved_by=reviewer,
            approved_at=now,
            updated_at=now,
        )
        db.add(article)
        db.flush()
        detail = f"Godkjent av {reviewer}. Ny artikkel #{article.id} lagt i kunnskapsdatabasen."

    proposal.status = "approved"
    proposal.reviewed_by = reviewer
    proposal.reviewed_at = now
    log_event(db, proposal.email_item_id, "approved", detail)
    db.commit()
    return article


def reject(db: Session, proposal_id: int, reviewer: str, reason: str) -> Proposal:
    proposal = _pending(db, proposal_id)
    proposal.status = "rejected"
    proposal.reviewed_by = reviewer
    proposal.reviewed_at = utcnow()
    # proposals-tabellen har ingen kolonne for begrunnelse, så den lagres i hendelsesloggen.
    log_event(db, proposal.email_item_id, "rejected", f"Avvist av {reviewer}. Begrunnelse: {reason}")
    db.commit()
    return proposal
