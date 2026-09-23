"""Databasetilkobling. Skjemaet opprettes av db/init.sql, ikke av Python-koden."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from . import config

# pool_pre_ping gjør at døde tilkoblinger oppdages hvis databasen startes på nytt.
engine = create_engine(config.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    """FastAPI-avhengighet: én databasesesjon per forespørsel."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
