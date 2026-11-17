# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A student project (IS-219, UiA) for JDD Utstyr, a Norwegian seller of vehicle lighting, car care and trailer and tractor accessories. It's a runnable Docker demo that turns fictional customer-service emails into anonymized knowledge articles, which an employee must approve. The spec is the source of truth: `docs/Prototypespesifikasjon JDD kunnskapsdatabase.pdf`. On top of it, the prototype implements the user stories from the group's Oppgave 4 (listed in `README.md`), notably story 3 (source and date on every answer) and story 5 (mark outdated articles). If something is unclear, ask instead of guessing. `README.md` (Norwegian) has the startup steps and the 5-minute demo script.

Hard rules from the spec:
- **All data is fictional.** No real customer data goes in.
- **Raw email is never stored.** `worker.process_message` anonymizes in memory before the first DB write. The AI anonymization pass only sees text the regex rules have already cleaned.
- **Nothing is written to `knowledge_articles` (or `article_versions`) except in `api/app/review.py` (employee approval and outdated/restore marking)** and in `seed_demo_articles()` in `db/seed.sql`.
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

The DB-backed tests (`test_no_pii_in_database`, `test_seed_data_gives_at_least_three_updates`, `test_review.py`) skip themselves when the DB is unreachable. `test_review.py` writes inside a transaction that is rolled back. Don't approve as a name that appears in `test_pii` (e.g. "Kari"), because reviewer names end up in `pipeline_events`. The PII test also skips when the DB has no data yet, so run `seed` first. Changes to `db/*.sql` only take effect after `down -v`.

## Architecture

- **web** (nginx, port 8080) serves the static `web/` files and proxies `/api/` to **api:8000**, so there is no CORS. There is no build step. `app.js` polls `/api/stats` and `/api/events` every 3 s, and tabs have hash routes (`#godkjenning`, `#forslag-<id>`).
- **api** (FastAPI, `api/app/main.py`) starts the ingest worker as a daemon thread in `lifespan`. Run a single uvicorn process so there is only one worker. `WORKER_ENABLED=false` disables the worker (used by tests).
- **Pipeline** (`worker.py`): poll Mailpit's REST API → `anonymize.anonymize` → insert into `email_items` → `extract.extract` → `duplicates.check` → insert a `Proposal(status="pending")`. Each step calls `models.log_event`, which writes a `pipeline_events` row. A failure sets `email_items.status='failed'` and logs step `failed`. Messages are processed on a 4-thread pool.
- **Thread format:** Mailpit delivers `Emne: <subject>` followed by the support reply, then `THREAD_SEPARATOR` (`----- Opprinnelig melding -----`), then the customer message. Text arrives with CRLF line endings, and `anonymize_rules` normalizes them. `seed/send_emails.py` and `tests/conftest.thread_text` must produce the same format.
- **Rule extraction** (`extract.py`): the SKU `DEMO-\d{3}` or the full product name gives the product. The problem is the customer's first real paragraph, and the solution is the support reply minus greeting and signature. Sentences that contain placeholders are dropped. `sanitize()` reruns the anonymization rules on every stored field, including edits the employee makes when approving.
- **Anonymization rules** (`anonymize.py`): order matters (order/ID/account numbers before phone; street address before postcode). Postcodes are only removed after an `[ADRESSE]` or alone on a line, so technical text like «2018 Toyota Hilux» or «9600 Lumen» survives; an 8-digit number after «varenummer» etc. is kept (`_phone`). A rule's replacement can be a function, and `counts` only counts real replacements. Every new placeholder must also be added to `extract.PLACEHOLDER` and the AI prompt.
- **Duplicate check / search:** `pg_trgm` `similarity(problem, :p)` against articles for the **same product** (all articles when the product is unknown). `score >= DUPLICATE_THRESHOLD` makes the proposal an `update`. After every approval, `review.recheck_pending` reruns the check for all other pending proposals and logs a `duplicate_check` event when the type or match changes. `duplicates.similar_pending` lists similar pending proposals in `GET /api/proposals/{id}`. Approving with `as_new` turns an `update` proposal into a new article. Approving an `update` first copies the old article into `article_versions`; an update without product keeps the article's product. `/api/demo/reset` must truncate `article_versions` too. `search.py` (used by `/api/articles` and the chatbot) combines `similarity`, `word_similarity` and ILIKE, and `chat.py` removes Norwegian stopwords before searching.
- **Outdated and source:** `knowledge_articles.status` is `active` or `outdated` (with `outdated_by/at/reason`). The chatbot calls `search_articles(..., only_active=True)`, while staff search shows outdated articles last with a warning. Approving an `update` proposal reactivates an outdated article. `proposals.article_id` is set on approval and links an article to its source emails (`search.article_sources`). Events `outdated` and `restored` have `email_item_id = NULL`.
- **Schema**: see `db/init.sql`. The ORM models in `models.py` mirror it; tables are never created from Python. There is no rejection-reason column, so the reason is stored in the `rejected` event's `detail`. Timestamps are naive UTC and serialized with a `Z` suffix.
- **LLM calls** (`llm.py`) use the Anthropic SDK (pinned to major version 2 in `requirements.txt`) with `output_config={"effort": "low"}`, plus a JSON schema for extraction and for chat answers (`answer`, `found`, `article_ids`; only the articles the model used are shown as sources). Current models reject `temperature` and assistant prefill. Chat sources never include the reviewer's name (customers see «godkjent av kundesenteret»).

## Seed data constraints

`seed/emails/*.json` hold `from`, `subject`, `customer_message`, `support_reply` and `test_pii`, the list of fictional personal data that tests assert is gone. The spec requires at least 3 emails that match an existing seed article (`update`) and at least 1 with no recognizable product. Keep the customer's first paragraph short and close to the seed article's `problem` wording, or trigram similarity falls below 0.4.
