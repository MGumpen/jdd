"""Steg 4: duplikatsjekk med pg_trgm.

similarity(a, b) i Postgres gir et tall mellom 0 og 1 basert på hvor mange
tre-bokstavs-biter (trigrammer) tekstene har felles. Vi sammenligner problemet
i forslaget med problemet i godkjente artikler for samme produkt. Er produktet
ukjent, sammenligner vi med alle artikler.

Sjekken kjøres når e-posten kommer inn, og på nytt for ventende forslag hver
gang en artikkel godkjennes (se review.recheck_pending). Da blir to like
forslag ikke to like artikler.
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
    """Returnerer (artikkel-id, likhet) for den mest like artikkelen, eller None.
    Uten produkt sammenlignes det med artikler for alle produkter."""
    same_product = "WHERE product_id = :product_id" if product_id is not None else ""
    row = db.execute(
        text(
            f"""
            SELECT id, similarity(problem, :problem) AS sim
            FROM knowledge_articles
            {same_product}
            ORDER BY sim DESC, id
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


def describe(dup: DuplicateResult) -> str:
    """Forklaring av resultatet til hendelsesloggen."""
    threshold = config.DUPLICATE_THRESHOLD
    if dup.proposal_type == "update":
        return (
            f"Ligner artikkel #{dup.matched_article_id} ({dup.similarity:.0%} likhet ≥ {threshold:.0%}). "
            "Foreslår oppdatering."
        )
    if dup.similarity is not None:
        return (
            f"Nærmeste artikkel #{dup.best_article_id} har {dup.similarity:.0%} likhet "
            f"(under {threshold:.0%}). Foreslår ny artikkel."
        )
    return "Ingen artikler å sammenligne med. Foreslår ny artikkel."


def similar_pending(db: Session, proposal_id: int, problem: str, product_id: int | None) -> list[dict]:
    """Andre ventende forslag som ligner på dette, slik at den ansatte ser dem før godkjenning.
    Sammenligner med forslag for samme produkt og forslag uten produkt (alle, hvis produktet er ukjent)."""
    same_product = "AND (product_id = :product_id OR product_id IS NULL)" if product_id is not None else ""
    rows = db.execute(
        text(
            f"""
            SELECT id, title, similarity(problem, :problem) AS sim
            FROM proposals
            WHERE status = 'pending' AND id <> :id {same_product}
              AND similarity(problem, :problem) >= :threshold
            ORDER BY sim DESC, id
            """
        ),
        {"id": proposal_id, "problem": problem, "product_id": product_id, "threshold": config.DUPLICATE_THRESHOLD},
    ).mappings()
    return [{"id": r["id"], "title": r["title"], "similarity": round(float(r["sim"]), 3)} for r in rows]
