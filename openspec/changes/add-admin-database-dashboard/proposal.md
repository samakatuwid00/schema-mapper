# Add Admin Database Dashboard

## Why

Every operational task — schema scans, onboarding, mapping review, deployment, refresh,
worker runs, replay, kill switches, migrations — currently requires typing a separate
terminal command from a README that has drifted from reality (several documented scripts
no longer exist). Operations are unaudited free-text (`--by <anything>`), give no live
visibility into queue depth or drift, and destructive actions (staging table drop +
recreate) are one keystroke away with no confirmation. Database admins need one guarded,
observable place to run all of it.

## What Changes

- New FastAPI backend (`src/admin_api`) exposing typed endpoints that wrap the existing
  Python workflow logic in-process (pipeline discover/propose/review/resolve/deploy/
  backfill/reconcile/monitor/refresh, worker runs, integration admin actions, schema
  monitor). No arbitrary shell execution — allowlisted job types only.
- New React + TypeScript + Vite admin frontend (`web/`) with pages: Overview, Schema
  Scanner, Onboarding Wizard, Mapping Review, Migrations, Worker & Queues, Drift
  Reports, Audit Log. Database-focused UI: schema trees, table grids, source-to-target
  mapping lanes, queue health cards, status chips, guarded action modals.
- Durable job orchestration: `integration.admin_job` + `integration.admin_job_event`
  tables, background execution, live job event streaming (SSE) to the browser.
- Guarded one-click behavior: one click starts safe workflows; deploy, refresh, replay,
  and schema approval additionally require a confirmation step with authenticated actor
  and a reason.
- Migration management: `integration.schema_migrations` tracking table with checksums,
  apply-from-UI under an advisory lock, and idempotency fixes to
  `sql/001_integration_foundation.sql` (missing `IF NOT EXISTS`).
- Per-admin authentication (session-based, bcrypt) with `operator` and `admin` roles;
  every mutating endpoint writes an `integration.admin_action_audit` row in the same
  transaction as the mutation.
- Refactor `src/pipeline.py` command bodies into reusable service functions returning
  structured results (CLI behavior unchanged — existing `cmd_*` become thin wrappers).

## Capabilities

### New Capabilities

- `admin-dashboard`: web UI centralizing all admin workflows with real-time status,
  database-themed layout, and guarded action modals.
- `job-orchestration`: durable, allowlisted background jobs with progress events,
  concurrency guards, and live streaming to clients.
- `schema-observability`: on-demand and scheduled schema scanning, fingerprint diffing,
  drift reports, and queue/entity health monitoring via API.
- `migration-management`: tracked, checksummed, lock-guarded application of SQL
  migration files from the UI.
- `audit-and-approval`: per-admin identity, role checks, confirmation contracts for
  dangerous actions, and a uniform audit trail for every mutation.

### Modified Capabilities

<!-- none - openspec/specs/ is empty; all capabilities are new -->

## Impact

- New dependencies: `fastapi`, `uvicorn[standard]`, `sse-starlette`, `passlib[bcrypt]`,
  `itsdangerous` (requirements.txt); Node toolchain for `web/` (Vite, React, TypeScript).
- Refactored: `src/pipeline.py` (extract service functions; add advisory lock +
  `deploying` status to deploy), `src/worker.py` (loop controllable via stop event).
- New SQL: `sql/003_admin_ui.sql` (admin_user, admin_action_audit, admin_job,
  admin_job_event, schema_migrations tables); `IF NOT EXISTS` fixes in
  `sql/001_integration_foundation.sql`; both mounted in `docker-compose.yml`.
- New env vars: `ADMIN_API_HOST`, `ADMIN_API_PORT`, `ADMIN_SESSION_SECRET`.
- Out of scope: `import_irimsv_data.sh` / `import_lrmis_schema.sh` (schema-dropping
  bootstrap scripts stay terminal-only by design); LRMIS production access; Alembic.
