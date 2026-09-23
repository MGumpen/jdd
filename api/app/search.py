"""Søk i godkjente artikler. Brukes både av søkesiden og av chatboten.

Poengsummen er den høyeste av:
- similarity mot tittel og problem (hele teksten ligner på søket), og
- word_similarity (søket ligner på en bit av artikkelteksten).
I tillegg tas treff med ILIKE (vanlig delstreng-søk) alltid med.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

# Hvor lik en artikkel må være for å regnes som et treff når ILIKE ikke treffer.
MIN_SCORE = 0.3

_SCORE = """
    GREATEST(
        similarity(a.title, :q),
        similarity(a.problem, :q),
        word_similarity(:q, a.title || ' ' || a.problem || ' ' || coalesce(a.tags, ''))
    )
"""


def search_articles(db: Session, q: str = "", product_id: int | None = None, limit: int = 50) -> list[dict]:
    q = (q or "").strip()
    where = []
    params: dict = {"q": q, "limit": limit}

    if product_id is not None:
        where.append("a.product_id = :product_id")
        params["product_id"] = product_id
    if q:
        params["like"] = f"%{q}%"
        params["min_score"] = MIN_SCORE
        where.append(
            f"(a.title ILIKE :like OR a.problem ILIKE :like OR a.tags ILIKE :like OR {_SCORE} >= :min_score)"
        )

    sql = f"""
        SELECT a.id, a.title, a.problem, a.solution, a.tags, a.version,
               a.approved_by, a.approved_at, a.updated_at, a.product_id,
               p.sku AS product_sku, p.name AS product_name,
               {_SCORE if q else "0"} AS score
        FROM knowledge_articles a
        LEFT JOIN products p ON p.id = a.product_id
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY score DESC, a.updated_at DESC
        LIMIT :limit
    """
    rows = db.execute(text(sql), params).mappings().all()
    return [
        {
            **row,
            "score": round(float(row["score"]), 3),
            # Tidspunkter lagres i UTC; «Z» gjør at nettleseren viser lokal tid.
            "approved_at": row["approved_at"].isoformat(timespec="seconds") + "Z",
            "updated_at": row["updated_at"].isoformat(timespec="seconds") + "Z",
        }
        for row in rows
    ]
