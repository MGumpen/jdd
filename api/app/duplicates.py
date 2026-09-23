"""Steg 4: duplikatsjekk med pg_trgm.

similarity(a, b) i Postgres gir et tall mellom 0 og 1 basert på hvor mange
tre-bokstavs-biter (trigrammer) tekstene har felles. Vi sammenligner problemet
i forslaget med problemet i godkjente artikler for samme produkt.
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import config


@dataclass
class DuplicateResult:
    proposal_type: str              # "new" eller "update"
    matched_article_id: int | None  # satt bare for "update"
    similarity: float | None        # høyeste likhet som ble funnet (også for "new")
    best_article_id: int | None     # artikkelen med høyest likhet, uansett terskel


def decide(best_similarity: float | None, threshold: float) -> str:
    """Selve regelen: over eller lik terskelen betyr at artikkelen bør oppdateres."""
    if best_similarity is not None and best_similarity >= threshold:
        return "update"
    return "new"


def find_best_match(db: Session, problem: str, product_id: int | None) -> tuple[int, float] | None:
    """Returnerer (artikkel-id, likhet) for den mest like artikkelen, eller None."""
    if product_id is None:
        return None  # uten produkt har vi ingen artikler å sammenligne mot
    row = db.execute(
        text(
            """
            SELECT id, similarity(problem, :problem) AS sim
            FROM knowledge_articles
            WHERE product_id = :product_id
            ORDER BY sim DESC
            LIMIT 1
            """
        ),
        {"problem": problem, "product_id": product_id},
    ).first()
    return (row.id, float(row.sim)) if row else None


def check(db: Session, problem: str, product_id: int | None) -> DuplicateResult:
    match = find_best_match(db, problem, product_id)
    best_id, best_sim = match if match else (None, None)
    proposal_type = decide(best_sim, config.DUPLICATE_THRESHOLD)
    return DuplicateResult(
        proposal_type=proposal_type,
        matched_article_id=best_id if proposal_type == "update" else None,
        similarity=round(best_sim, 3) if best_sim is not None else None,
        best_article_id=best_id,
    )
