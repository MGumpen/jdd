"""Tester for godkjenning, kilde og utdatert-merking (brukerhistorie 3 og 5).

Testene kjører mot databasen, men inne i en transaksjon som rulles tilbake til slutt.
Demo-dataene blir derfor ikke endret. Uten database hoppes testene over.
"""

import os

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app import chat, config, duplicates, review
from app.models import KnowledgeArticle, PipelineEvent, Product, Proposal
from app.search import article_sources, article_versions, search_articles

QUESTION = "Hvorfor flimrer lysbjelken når jeg starter bilen?"


@pytest.fixture
def db(monkeypatch):
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL er ikke satt")
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 3})
        conn = engine.connect()
    except Exception:  # noqa: BLE001
        pytest.skip("Databasen er ikke tilgjengelig")
    has_status = conn.execute(text(
        "SELECT 1 FROM information_schema.columns WHERE table_name = 'knowledge_articles' AND column_name = 'status'"
    )).first()
    conn.rollback()  # avslutt transaksjonen spørringen over startet
    if not has_status:
        conn.close()
        pytest.skip("Databasen har gammelt skjema. Kjør «docker compose down -v» og start på nytt.")

    # Testene skal ikke kalle KI-tjenesten, selv om ANTHROPIC_API_KEY er satt.
    monkeypatch.setattr(config, "ai_enabled", lambda: False)

    outer = conn.begin()
    # commit() i review.py blir da bare et «savepoint» inne i den ytre transaksjonen.
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    outer.rollback()
    conn.close()
    engine.dispose()


def _flimmer_article(db: Session) -> KnowledgeArticle:
    """Startartikkelen «Lysbjelke flimrer ved motorstart» fra db/seed.sql."""
    article = db.scalars(
        select(KnowledgeArticle).join(Product).where(Product.sku == "DEMO-001").order_by(KnowledgeArticle.id)
    ).first()
    if article is None:
        pytest.skip("Fant ikke startartikkelen for DEMO-001")
    if article.status == "outdated":
        review.restore(db, article.id, "Test")
    return article


def test_outdated_article_is_not_used_by_chatbot(db):
    article = _flimmer_article(db)
    assert article.id in [s["id"] for s in chat.answer(db, QUESTION)["sources"]]

    review.mark_outdated(db, article.id, "Test", "Gjelder ikke nye modeller")

    assert article.id not in [s["id"] for s in chat.answer(db, QUESTION)["sources"]]
    # Ansatte ser den fortsatt i søket, men merket som utdatert.
    hit = next(a for a in search_articles(db, "flimrer") if a["id"] == article.id)
    assert hit["status"] == "outdated"
    assert hit["outdated_by"] == "Test"
    assert hit["outdated_reason"] == "Gjelder ikke nye modeller"
    event = db.scalars(select(PipelineEvent).order_by(PipelineEvent.id.desc())).first()
    assert event.step == "outdated" and f"#{article.id}" in event.detail


def test_cannot_mark_outdated_twice(db):
    article = _flimmer_article(db)
    review.mark_outdated(db, article.id, "Test", "Gammel")
    with pytest.raises(review.ReviewError) as exc:
        review.mark_outdated(db, article.id, "Test", "Gammel")
    assert exc.value.status_code == 409


def test_restore_makes_article_active(db):
    article = _flimmer_article(db)
    review.mark_outdated(db, article.id, "Test", "Feil merking")
    review.restore(db, article.id, "Test")
    assert article.status == "active"
    assert article.outdated_by is None and article.outdated_reason is None
    assert article.id in [s["id"] for s in chat.answer(db, QUESTION)["sources"]]


def test_outdated_reason_is_anonymized(db):
    article = _flimmer_article(db)
    review.mark_outdated(db, article.id, "Test", "Kunden ringte fra 912 34 567 og sa at den er feil")
    assert "912 34 567" not in article.outdated_reason
    assert "[TELEFON]" in article.outdated_reason


def test_approved_update_reactivates_article_and_records_source(db):
    article = _flimmer_article(db)
    version = article.version
    review.mark_outdated(db, article.id, "Test", "Løsningen er gammel")

    proposal = Proposal(
        product_id=article.product_id, title=article.title, problem=article.problem,
        solution="Ny løsning: bruk relé på tenningsstrøm.", tags=article.tags,
        proposal_type="update", matched_article_id=article.id, similarity=0.9,
    )
    db.add(proposal)
    db.flush()

    review.approve(db, proposal.id, "Test", {})

    assert article.status == "active"
    assert article.version == version + 1
    assert proposal.article_id == article.id
    # Det nye forslaget er nå en av kildene til artikkelen (den nyeste).
    assert article_sources(db, [article.id])[article.id][-1]["proposal_id"] == proposal.id


def test_approved_new_proposal_records_source(db):
    proposal = Proposal(
        product_id=None, title="Testartikkel", problem="Et problem", solution="En løsning",
        tags="test", proposal_type="new",
    )
    db.add(proposal)
    db.flush()

    article = review.approve(db, proposal.id, "Test", {})

    assert proposal.article_id == article.id
    sources = article_sources(db, [article.id])[article.id]
    assert len(sources) == 1 and sources[0]["reviewed_by"] == "Test"


def _pending_proposal(db: Session, **fields) -> Proposal:
    """Lager et ventende forslag direkte i databasen (uten e-post)."""
    values = {"title": "Testforslag", "solution": "En løsning", "tags": "test", "proposal_type": "new"} | fields
    proposal = Proposal(**values)
    db.add(proposal)
    db.flush()
    return proposal


def test_update_keeps_previous_version(db):
    article = _flimmer_article(db)
    old_version, old_solution = article.version, article.solution
    proposal = _pending_proposal(
        db, product_id=article.product_id, problem=article.problem, solution="Helt ny løsning fra kundeservice.",
        proposal_type="update", matched_article_id=article.id, similarity=0.9,
    )

    review.approve(db, proposal.id, "Test", {})

    assert article.version == old_version + 1
    assert article.solution == "Helt ny løsning fra kundeservice."
    # Den gamle teksten er tatt vare på, og vises i søket.
    previous = article_versions(db, [article.id])[article.id]
    assert previous[0]["version"] == old_version
    assert previous[0]["solution"] == old_solution
    hit = next(a for a in search_articles(db, "flimrer") if a["id"] == article.id)
    assert hit["previous_versions"][0]["version"] == old_version


def test_update_without_product_keeps_article_product(db):
    article = _flimmer_article(db)
    product_id = article.product_id
    proposal = _pending_proposal(
        db, product_id=None, problem=article.problem,
        proposal_type="update", matched_article_id=article.id, similarity=0.9,
    )
    review.approve(db, proposal.id, "Test", {"product_id": None})
    assert article.product_id == product_id


def test_approve_as_new_instead_of_update(db):
    article = _flimmer_article(db)
    old_version = article.version
    proposal = _pending_proposal(
        db, product_id=article.product_id, problem="Lysbjelken lyser svakt på den ene siden.",
        proposal_type="update", matched_article_id=article.id, similarity=0.45,
    )

    new_article = review.approve(db, proposal.id, "Test", {}, as_new=True)

    assert new_article.id != article.id
    assert article.version == old_version  # den matchede artikkelen er ikke endret
    assert proposal.proposal_type == "new" and proposal.matched_article_id is None
    event = db.scalars(select(PipelineEvent).where(PipelineEvent.step == "approved").order_by(PipelineEvent.id.desc())).first()
    assert "valgte ny artikkel" in event.detail


def test_approval_rechecks_other_pending_proposals(db):
    """To like forslag skal ikke bli to like artikler: når det første godkjennes,
    blir det andre en oppdatering av den nye artikkelen."""
    product_id = db.scalar(select(Product.id).where(Product.sku == "DEMO-007"))
    problem = "Bilshampoen skummer ikke når vannet er veldig kaldt om vinteren."
    first = _pending_proposal(db, product_id=product_id, problem=problem)
    second = _pending_proposal(db, product_id=product_id, problem=problem)
    # Den ansatte ser det lignende forslaget før godkjenning.
    assert second.id in [p["id"] for p in duplicates.similar_pending(db, first.id, problem, product_id)]

    article = review.approve(db, first.id, "Test", {})

    assert second.proposal_type == "update"
    assert second.matched_article_id == article.id
    assert float(second.similarity) == 1.0
    assert duplicates.similar_pending(db, second.id, problem, product_id) == []


def test_chat_sources_hide_staff_names(db):
    for source in chat.answer(db, QUESTION)["sources"]:
        assert "approved_by" not in source
        assert source["updated_at"]


def test_ai_chat_shows_only_sources_it_used(db, monkeypatch):
    """KI-modus (uten ekte KI-kall): bare artiklene modellen brukte vises som kilde,
    og ingen kilder når den ikke fant svaret."""
    article = _flimmer_article(db)
    monkeypatch.setattr(config, "ai_enabled", lambda: True)

    reply = {"answer": "Koble lysbjelken via relé.", "found": True, "article_ids": [article.id]}
    monkeypatch.setattr(chat.llm, "complete_json", lambda *args, **kwargs: reply)
    result = chat.answer(db, QUESTION)
    assert result["mode"] == "KI"
    assert [s["id"] for s in result["sources"]] == [article.id]

    reply = {"answer": "Jeg fant ikke svaret.", "found": False, "article_ids": []}
    monkeypatch.setattr(chat.llm, "complete_json", lambda *args, **kwargs: reply)
    assert chat.answer(db, QUESTION)["sources"] == []


def test_rejection_reason_is_anonymized(db):
    proposal = _pending_proposal(db, product_id=None, problem="Et problem som skal avvises")
    review.reject(db, proposal.id, "Test", "Kunden ringte fra 912 34 567, dette er feil")
    event = db.scalars(select(PipelineEvent).where(PipelineEvent.step == "rejected").order_by(PipelineEvent.id.desc())).first()
    assert "912 34 567" not in event.detail
    assert "[TELEFON]" in event.detail
