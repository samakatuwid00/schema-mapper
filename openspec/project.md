# Project Context

## Purpose

Region V IRIMSV-to-LRMIS integration ("schema_mapper"). Keeps the IRIMSV PostgreSQL
schema authoritative, transforms approved records into the LRMIS contract shape, and
idempotently publishes them to an LRMIS-owned MySQL staging database. The service never
writes to LRMIS production.

## Tech Stack

- Python 3.12+ (synchronous), psycopg2 (PostgreSQL), mysql-connector-python (MySQL)
- google-genai (Gemini) for AI-drafted mapping proposals — human approval is a hard gate
- sqlglot for DDL parsing, rich for terminal output, pytest for tests
- Docker Compose dev stack: `central_db` (Postgres 16, port 5433) and
  `lrmis_staging_db` (MySQL 8.4, port 3307)
- Admin UI (this change): FastAPI + uvicorn backend under `src/admin_api`,
  React + TypeScript + Vite frontend under `web/`

## Project Conventions

- Control-plane state lives in the Postgres schema `integration` (outbox, quarantine,
  onboarding entities/proposals/field reviews, audits, schema versions, entity kill
  switches, drift reports). Source data lives in the `irimsv` schema of the same
  Postgres instance; the MySQL staging database is write-only via least-privilege
  upsert/bulk-insert.
- Connection pooling via `src/connectors.py` (`PostgresCentralConnector`,
  `MySQLStagingConnector`). New code must use these, never ad hoc connections.
- Every outbound record is keyed by an immutable `external_reference` UUID; MySQL
  delivery upserts on it, which is what makes retries and replay safe.
- Mutations that matter (approvals, deploys, kill switches) are attributed to a named
  human actor and audited.
- CLI entry points: `python -m src.pipeline <subcommand>`, `python -m src.worker`,
  `scripts/integration_admin.py`, `scripts/schema_monitor.py`. The admin API wraps the
  same underlying service functions in-process — it does not shell out.

## Agent Context Systems

- `graphify-out/graph.json` — knowledge graph of this repo. Query with
  `graphify query "<question>"`, `graphify path "<A>" "<B>"`, `graphify explain "<X>"`
  before broad file reads. Refresh after code changes with `graphify update .`.
- `CLAUDE.md` — agent instruction entrypoint.
- `openspec/` — spec-driven changes. Validate with
  `openspec validate <change> --strict`.

## Domain Glossary

- **IRIMSV**: Region V data-entry system (authoritative source, PostgreSQL).
- **LRMIS**: target system; integration writes only to its MySQL *staging* database.
- **Outbox**: `integration.outbox` event queue (pending/processing/delivered/retry/
  quarantined/dead_letter) claimed with `FOR UPDATE SKIP LOCKED`.
- **Onboarding**: discover → propose (AI) → review/resolve → deploy → backfill flow for
  bringing a source table under sync.
- **Drift**: a change in the fingerprinted source/target schema; breaking drift pauses
  affected entities automatically.
- **Kill switch**: `integration.entity_control.enabled` — per-entity delivery gate.
