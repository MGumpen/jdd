"""Tester for duplikatsjekken."""

import os

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.anonymize import anonymize_rules
from app.duplicates import decide
from app.extract import extract_with_rules
from app.models import Product

from .conftest import load_seed_emails, thread_text


def test_decide_uses_threshold():
    assert decide(None, 0.4) == "new"      # ingen artikler for produktet
    assert decide(0.39, 0.4) == "new"
    assert decide(0.4, 0.4) == "update"    # lik terskelen regnes som duplikat
    assert decide(0.9, 0.4) == "update"


@pytest.fixture(scope="module")
def conn():
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL er ikke satt")
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 3})
        connection = engine.connect()
    except Exception:  # noqa: BLE001
        pytest.skip("Databasen er ikke tilgjengelig")
    yield connection
    connection.close()
    engine.dispose()


def _best_similarity(conn, problem: str, sku: str | None) -> float | None:
    return conn.execute(
        text(
            """
            SELECT max(similarity(a.problem, :problem))
            FROM knowledge_articles a JOIN products p ON p.id = a.product_id
            WHERE p.sku = :sku
            """
        ),
        {"problem": problem, "sku": sku},
    ).scalar()


def test_seed_data_gives_at_least_three_updates(conn):
    """Akseptansekriterium: minst én (vi krever tre) e-post gir forslag av typen «update»
    i regelmodus, og minst én e-post mangler gjenkjennelig produkt."""
    threshold = float(os.getenv("DUPLICATE_THRESHOLD", "0.4"))
    products = list(Session(bind=conn).scalars(select(Product)))
    updates, without_product = 0, 0
    for email in load_seed_emails():
        result = extract_with_rules(anonymize_rules(thread_text(email))[0], products)
        if result.sku is None:
            without_product += 1
            continue
        if decide(_best_similarity(conn, result.problem, result.sku), threshold) == "update":
            updates += 1
    assert updates >= 3
    assert without_product >= 1
