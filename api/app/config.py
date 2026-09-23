"""Innstillinger som leses fra miljøvariabler (se .env.example)."""

import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://jdd:jdd@localhost:5432/jdd")
MAILPIT_URL = os.getenv("MAILPIT_URL", "http://localhost:8025").rstrip("/")

# Valgfri. Uten nøkkel kjører prototypen i regelbasert modus.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5").strip() or "claude-sonnet-5"

POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "10"))
DUPLICATE_THRESHOLD = float(os.getenv("DUPLICATE_THRESHOLD", "0.4"))

# Settes til "false" for å kjøre API-et uten bakgrunnsjobben (f.eks. under tester).
WORKER_ENABLED = os.getenv("WORKER_ENABLED", "true").lower() != "false"


def ai_enabled() -> bool:
    """KI-modus er på bare når en API-nøkkel er satt."""
    return bool(ANTHROPIC_API_KEY)
