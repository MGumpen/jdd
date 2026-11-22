"""Søk i godkjente artikler. Brukes både av søkesiden og av chatboten.

Poengsummen er den høyeste av:
- similarity mot tittel og problem (hele teksten ligner på søket), og
- word_similarity (søket ligner på en bit av artikkelteksten).
I tillegg tas treff med ILIKE (vanlig delstreng-søk) alltid med.

Utdaterte artikler vises sist med advarsel i søket for ansatte, men chatboten
ber bare om aktive artikler (only_active=True).

Hver artikkel får med seg kildene sine: e-postene (anonymisert) som ble godkjent
inn i artikkelen, med dato og hvem som godkjente (brukerhistorie 3).
"""

from datetime import datetime

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


def iso(value: datetime | None) -> str | None:
    """Tidspunkter lagres i UTC; «Z» gjør at nettleseren viser lokal tid."""
    return value.isoformat(timespec="seconds") + "Z" if value else None


def article_sources(db: Session, article_ids: list[int]) -> dict[int, list[dict]]:
    """Henter kildene (godkjente forslag og e-postene bak dem) for flere artikler på én gang.
    Startartiklene fra seed.sql har ingen e-post som kilde."""
    if not article_ids:
        return {}
    rows = db.execute(
        text(
            """
            SELECT p.article_id, p.id AS proposal_id, p.email_item_id, p.proposal_type,
                   p.reviewed_by, p.reviewed_at, e.created_at AS received_at
            FROM proposals p
            LEFT JOIN email_items e ON e.id = p.email_item_id
            WHERE p.status = 'approved' AND p.article_id = ANY(:ids)
            ORDER BY p.reviewed_at, p.id
            """
        ),
        {"ids": list(article_ids)},
    ).mappings()
    sources: dict[int, list[dict]] = {i: [] for i in article_ids}
    for row in rows:
        sources[row["article_id"]].append({
            "proposal_id": row["proposal_id"],
            "email_item_id": row["email_item_id"],
            "proposal_type": row["proposal_type"],
            "reviewed_by": row["reviewed_by"],
            "reviewed_at": iso(row["reviewed_at"]),
            "received_at": iso(row["received_at"]),
        })
    return sources


def article_versions(db: Session, article_ids: list[int]) -> dict[int, list[dict]]:
    """Tidligere versjoner av flere artikler på én gang, nyeste først."""
    if not article_ids:
        return {}
    rows = db.execute(
        text(
            """
            SELECT article_id, version, title, problem, solution, tags,
                   approved_by, approved_at, replaced_at
            FROM article_versions
            WHERE article_id = ANY(:ids)
            ORDER BY article_id, version DESC
            """
        ),
        {"ids": list(article_ids)},
    ).mappings()
    versions: dict[int, list[dict]] = {i: [] for i in article_ids}
    for row in rows:
        versions[row["article_id"]].append({
            "version": row["version"],
            "title": row["title"],
            "problem": row["problem"],
            "solution": row["solution"],
            "tags": row["tags"],
            "approved_by": row["approved_by"],
            "approved_at": iso(row["approved_at"]),
            "replaced_at": iso(row["replaced_at"]),
        })
    return versions


def search_articles(
    db: Session, q: str = "", product_id: int | None = None, limit: int = 50, only_active: bool = False
) -> list[dict]:
    q = (q or "").strip()
    where = []
    params: dict = {"q": q, "limit": limit}

    if only_active:
        where.append("a.status = 'active'")
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
               a.status, a.outdated_by, a.outdated_at, a.outdated_reason,
               p.sku AS product_sku, p.name AS product_name,
               {_SCORE if q else "0"} AS score
        FROM knowledge_articles a
        LEFT JOIN products p ON p.id = a.product_id
        {"WHERE " + " AND ".join(where) if where else ""}
        -- Aktive artikler først, så de beste treffene, så de nyeste.
        ORDER BY (a.status = 'active') DESC, score DESC, a.updated_at DESC
        LIMIT :limit
    """
    rows = db.execute(text(sql), params).mappings().all()
    ids = [row["id"] for row in rows]
    sources = article_sources(db, ids)
    versions = article_versions(db, ids)
    return [
        {
            **row,
            "score": round(float(row["score"]), 3),
            "approved_at": iso(row["approved_at"]),
            "updated_at": iso(row["updated_at"]),
            "outdated_at": iso(row["outdated_at"]),
            "sources": sources[row["id"]],
            "previous_versions": versions[row["id"]],
        }
        for row in rows
    ]
