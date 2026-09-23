# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A student project (IS-219, UiA) for JDD Utstyr, a Norwegian seller of vehicle lighting, car care and trailer and tractor accessories. It's a runnable Docker demo that turns fictional customer-service emails into anonymized knowledge articles, which an employee must approve. The spec is the source of truth: `docs/Prototypespesifikasjon JDD kunnskapsdatabase.pdf`. If something is unclear, ask instead of guessing. `README.md` (Norwegian) has the startup steps and the 5-minute demo script.

Hard rules from the spec:
- **All data is fictional.** No real customer data goes in.
- **Raw email is never stored.** `worker.process_message` anonymizes in memory before the first DB write. The AI anonymization pass only sees text the regex rules have already cleaned.
- **Nothing is written to `knowledge_articles` except in `api/app/review.py` (employee approval)** and in `seed_demo_articles()` in `db/seed.sql`.
- **Must work without `ANTHROPIC_API_KEY` (rule mode).** Every AI call (`anonymize.py`, `extract.py`, `chat.py`) catches exceptions and falls back to the rule-based path, noting the fallback in the event log. Keep it that way.
- **The UI and user-facing strings are in Norwegian (bokmål).** Code comments are in Norwegian too, because the students must be able to explain the code. Keep the code simple and commented.

## Commands

```sh
cp .env.example .env
docker compose up --build                     # mailpit, db, api (+ worker), web, adminer
docker compose run --rm seed                  # send the 10 seed emails to Mailpit (2 s apart; SEED_DELAY_SECONDS)
docker compose run --rm api pytest            # all tests (runs in the api image)
docker compose run --rm api pytest tests/test_anonymize.py::test_rules -v   # a single test
docker compose up --build -d api              # rebuild after Python changes (code is baked into the image, no reload)
docker compose down -v                        # also drop the DB volume; init.sql and seed.sql rerun on next start
curl -X POST localhost:8080/api/demo/reset    # clear emails, proposals, events and new articles, and empty Mailpit
```

The DB-backed tests (`test_no_pii_in_database`, `test_seed_data_gives_at_least_three_updates`) skip themselves when the DB is unreachable. The PII test also skips when the DB has no data yet, so run `seed` first. Changes to `db/*.sql` only take effect after `down -v`.

## Architecture

- **web** (nginx, port 8080) serves the static `web/` files and proxies `/api/` to **api:8000**, so there is no CORS. There is no build step. `app.js` polls `/api/stats` and `/api/events` every 3 s, and tabs have hash routes (`#godkjenning`, `#forslag-<id>`).
- **api** (FastAPI, `api/app/main.py`) starts the ingest worker as a daemon thread in `lifespan`. Run a single uvicorn process so there is only one worker. `WORKER_ENABLED=false` disables the worker (used by tests).
- **Pipeline** (`worker.py`): poll Mailpit's REST API → `anonymize.anonymize` → insert into `email_items` → `extract.extract` → `duplicates.check` → insert a `Proposal(status="pending")`. Each step calls `models.log_event`, which writes a `pipeline_events` row. A failure sets `email_items.status='failed'` and logs step `failed`. Messages are processed on a 4-thread pool.
- **Thread format:** Mailpit delivers `Emne: <subject>` followed by the support reply, then `THREAD_SEPARATOR` (`----- Opprinnelig melding -----`), then the customer message. Text arrives with CRLF line endings, and `anonymize_rules` normalizes them. `seed/send_emails.py` and `tests/conftest.thread_text` must produce the same format.
- **Rule extraction** (`extract.py`): the SKU `DEMO-\d{3}` or the full product name gives the product. The problem is the customer's first real paragraph, and the solution is the support reply minus greeting and signature. Sentences that contain placeholders are dropped. `sanitize()` reruns the anonymization rules on every stored field, including edits the employee makes when approving.
- **Duplicate check / search:** `pg_trgm` `similarity(problem, :p)` against articles for the **same product only**. `score >= DUPLICATE_THRESHOLD` makes the proposal an `update`. `search.py` (used by `/api/articles` and the chatbot) combines `similarity`, `word_similarity` and ILIKE, and `chat.py` removes Norwegian stopwords before searching.
- **Schema**: see `db/init.sql`. The ORM models in `models.py` mirror it; tables are never created from Python. There is no rejection-reason column, so the reason is stored in the `rejected` event's `detail`. Timestamps are naive UTC and serialized with a `Z` suffix.
- **LLM calls** (`llm.py`) use the Anthropic SDK with `output_config={"effort": "low"}`, plus a JSON schema for extraction. Current models reject `temperature` and assistant prefill.

## Seed data constraints

`seed/emails/*.json` hold `from`, `subject`, `customer_message`, `support_reply` and `test_pii`, the list of fictional personal data that tests assert is gone. The spec requires at least 3 emails that match an existing seed article (`update`) and at least 1 with no recognizable product. Keep the customer's first paragraph short and close to the seed article's `problem` wording, or trigram similarity falls below 0.4.
