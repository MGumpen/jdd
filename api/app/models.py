"""SQLAlchemy-modeller som speiler tabellene i db/init.sql."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Identity, Numeric, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    sku: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100))


class EmailItem(Base):
    """Anonymisert kopi av en e-posttråd. Rå e-post lagres aldri."""

    __tablename__ = "email_items"

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    source_msg_id: Mapped[str] = mapped_column(String(200), unique=True)
    anonymized_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class KnowledgeArticle(Base):
    __tablename__ = "knowledge_articles"

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    title: Mapped[str] = mapped_column(String(200))
    problem: Mapped[str] = mapped_column(Text)
    solution: Mapped[str] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(String(300))
    version: Mapped[int] = mapped_column(server_default="1")
    approved_by: Mapped[str] = mapped_column(String(100))
    approved_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())

    product: Mapped[Product | None] = relationship()


class Proposal(Base):
    """Forslag til ny eller oppdatert artikkel. Venter på godkjenning fra en ansatt."""

    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    email_item_id: Mapped[int | None] = mapped_column(ForeignKey("email_items.id"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    title: Mapped[str] = mapped_column(String(200))
    problem: Mapped[str] = mapped_column(Text)
    solution: Mapped[str] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(String(300))
    proposal_type: Mapped[str] = mapped_column(String(20))  # new, update
    matched_article_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_articles.id"))
    similarity: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    reviewed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    email_item: Mapped[EmailItem | None] = relationship()
    product: Mapped[Product | None] = relationship()
    matched_article: Mapped[KnowledgeArticle | None] = relationship()


class PipelineEvent(Base):
    """Én rad per steg i dataflyten. Vises live i Dataflyt-fanen."""

    __tablename__ = "pipeline_events"

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    email_item_id: Mapped[int | None] = mapped_column(ForeignKey("email_items.id"))
    step: Mapped[str] = mapped_column(String(30))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


def log_event(db, email_item_id: int | None, step: str, detail: str) -> None:
    """Legger til en hendelse i loggen. Kalleren står for commit."""
    db.add(PipelineEvent(email_item_id=email_item_id, step=step, detail=detail))
