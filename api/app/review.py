"""Godkjenning og avvisning av forslag. Det er BARE her artikler skrives.

- Godkjent «new»:    ny rad i knowledge_articles.
- Godkjent «update»: den gamle versjonen lagres i article_versions, og den matchede
                     artikkelen oppdateres og får version + 1. Den ansatte kan også
                     velge å lage en ny artikkel i stedet (as_new).
- Avvist:            forslaget merkes «rejected», begrunnelsen lagres i hendelsesloggen.
- Utdatert:          en ansatt merker en artikkel som utdatert (eller gjenoppretter den).
                     Utdaterte artikler vises med advarsel i søket og brukes ikke av chatboten.

Etter hver godkjenning kjøres duplikatsjekken på nytt for de andre ventende forslagene,
så et likt forslag blir en oppdatering av den nye artikkelen i stedet for en dublett.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import duplicates
from .anonymize import anonymize_rules
from .extract import Extraction, sanitize
from .models import ArticleVersion, KnowledgeArticle, Proposal, log_event


def utcnow() -> datetime:
    """Databasen lagrer tidspunkter i UTC uten tidssone (samme som now() i Postgres)."""
    return datetime.now(UTC).replace(tzinfo=None)


def _reactivate(article: KnowledgeArticle) -> None:
    """Fjerner utdatert-merket, f.eks. når artikkelen får ny, godkjent kunnskap."""
    article.status = "active"
    article.outdated_by = None
    article.outdated_at = None
    article.outdated_reason = None


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


def _save_version(db: Session, article: KnowledgeArticle, now: datetime) -> None:
    """Tar vare på artikkelen slik den er nå, før en oppdatering overskriver den."""
    db.add(ArticleVersion(
        article_id=article.id,
        version=article.version,
        product_id=article.product_id,
        title=article.title,
        problem=article.problem,
        solution=article.solution,
        tags=article.tags,
        approved_by=article.approved_by,
        approved_at=article.approved_at,
        replaced_at=now,
    ))


def recheck_pending(db: Session, approved_proposal_id: int) -> None:
    """Kjører duplikatsjekken på nytt for alle andre ventende forslag.
    Kalles etter en godkjenning, fordi en ny eller endret artikkel kan gjøre et
    ventende forslag til en oppdatering (eller omvendt)."""
    db.flush()  # sørg for at den nye/endrede artikkelen er med i sammenligningen
    pending = db.scalars(
        select(Proposal).where(Proposal.status == "pending", Proposal.id != approved_proposal_id)
    )
    for other in pending:
        dup = duplicates.check(db, other.problem, other.product_id)
        changed = (dup.proposal_type, dup.matched_article_id) != (other.proposal_type, other.matched_article_id)
        other.proposal_type = dup.proposal_type
        other.matched_article_id = dup.matched_article_id
        other.similarity = dup.similarity
        if changed:
            log_event(
                db, other.email_item_id, "duplicate_check",
                f"Forslag #{other.id} sjekket på nytt etter at forslag #{approved_proposal_id} ble godkjent: "
                f"{duplicates.describe(dup)}",
            )


def approve(db: Session, proposal_id: int, reviewer: str, edits: dict, as_new: bool = False) -> KnowledgeArticle:
    """Godkjenner et forslag. as_new=True betyr at den ansatte mener forslaget ikke handler
    om det samme som den matchede artikkelen, og vil ha en ny artikkel i stedet."""
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
    choice_note = ""
    if as_new and proposal.proposal_type == "update":
        # Den ansatte overstyrer duplikatsjekken. Likheten beholdes, så man ser hva systemet mente.
        choice_note = f" Den ansatte valgte ny artikkel i stedet for å oppdatere artikkel #{proposal.matched_article_id}."
        proposal.proposal_type = "new"
        proposal.matched_article_id = None

    article = db.get(KnowledgeArticle, proposal.matched_article_id) if proposal.proposal_type == "update" else None
    if article is not None:
        _save_version(db, article, now)  # den gamle teksten forsvinner ikke
        article.title = proposal.title
        article.problem = proposal.problem
        article.solution = proposal.solution
        article.tags = proposal.tags
        # Er produktet ukjent i forslaget, beholder artikkelen produktet sitt.
        if proposal.product_id is not None:
            article.product_id = proposal.product_id
        article.version = article.version + 1
        article.approved_by = reviewer
        article.approved_at = now
        article.updated_at = now
        detail = (
            f"Godkjent av {reviewer}. Artikkel #{article.id} oppdatert til versjon {article.version}. "
            f"Versjon {article.version - 1} er lagret som tidligere versjon."
        )
        # Ny kunnskap erstatter den gamle, så en utdatert artikkel blir aktiv igjen.
        if article.status == "outdated":
            _reactivate(article)
            detail += " Artikkelen var merket som utdatert og er nå aktiv igjen."
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
        detail = f"Godkjent av {reviewer}. Ny artikkel #{article.id} lagt i kunnskapsdatabasen.{choice_note}"

    proposal.status = "approved"
    proposal.article_id = article.id  # kilden: denne e-posten ligger bak artikkelen
    proposal.reviewed_by = reviewer
    proposal.reviewed_at = now
    log_event(db, proposal.email_item_id, "approved", detail)
    recheck_pending(db, proposal.id)
    db.commit()
    return article


def reject(db: Session, proposal_id: int, reviewer: str, reason: str) -> Proposal:
    proposal = _pending(db, proposal_id)
    # Begrunnelsen kjøres gjennom anonymiseringsreglene, som alle andre felt den ansatte skriver.
    reason = anonymize_rules(reason)[0].strip()
    proposal.status = "rejected"
    proposal.reviewed_by = reviewer
    proposal.reviewed_at = utcnow()
    # proposals-tabellen har ingen kolonne for begrunnelse, så den lagres i hendelsesloggen.
    log_event(db, proposal.email_item_id, "rejected", f"Avvist av {reviewer}. Begrunnelse: {reason}")
    db.commit()
    return proposal


def _article(db: Session, article_id: int) -> KnowledgeArticle:
    article = db.get(KnowledgeArticle, article_id, with_for_update=True)
    if article is None:
        raise ReviewError("Artikkelen finnes ikke", 404)
    return article


def mark_outdated(db: Session, article_id: int, reviewer: str, reason: str) -> KnowledgeArticle:
    """Brukerhistorie 5: merk utdatert informasjon, så kollegene ikke bruker gamle svar."""
    article = _article(db, article_id)
    if article.status == "outdated":
        raise ReviewError("Artikkelen er allerede merket som utdatert", 409)
    # Begrunnelsen kjøres gjennom anonymiseringsreglene, som alle andre felt den ansatte skriver.
    reason = anonymize_rules(reason)[0].strip()
    article.status = "outdated"
    article.outdated_by = reviewer
    article.outdated_at = utcnow()
    article.outdated_reason = reason
    log_event(db, None, "outdated",
              f"Artikkel #{article.id} «{article.title}» merket som utdatert av {reviewer}. Begrunnelse: {reason}")
    db.commit()
    return article


def restore(db: Session, article_id: int, reviewer: str) -> KnowledgeArticle:
    """Angrer utdatert-merket, f.eks. hvis det ble satt ved en feil."""
    article = _article(db, article_id)
    if article.status != "outdated":
        raise ReviewError("Artikkelen er ikke merket som utdatert", 409)
    _reactivate(article)
    log_event(db, None, "restored", f"Artikkel #{article.id} «{article.title}» gjenopprettet av {reviewer}.")
    db.commit()
    return article
