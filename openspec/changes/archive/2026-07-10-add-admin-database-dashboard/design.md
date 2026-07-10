# Design: Admin Database Dashboard

## Context

The repo is a synchronous Python CLI codebase with no web layer. Control-plane state
already lives in Postgres (`integration` schema) behind pooled connectors
(`src/connectors.py`), and several safety idioms already exist and must be reused, not
reinvented:

- `integration_store.claim_events` — `FOR UPDATE SKIP LOCKED` work-queue claiming.
- `integration_store.replay` — atomic compare-and-swap via conditional `UPDATE` +
  rowcount check.
- `mapping_repository.approve` — `SELECT ... FOR UPDATE` before status transitions.
- Idempotent MySQL upsert keyed on `external_reference` with `source_updated_at`
  out-of-order protection.

Known gaps this design must close: `cmd_deploy` has no row lock before its MySQL DDL
side effects; nothing tracks which `sql/*.sql` files were applied where;
`sql/001_integration_foundation.sql` lacks `IF NOT EXISTS`; `--by` actor strings are
unverified; refresh/redeploy drop staging tables with no snapshot.

## Goals / Non-Goals

**Goals**: one guarded web control panel for all existing workflows; durable observable
jobs; real-time queue/drift visibility; per-admin accountability; safe concurrent use
from multiple browser tabs/admins.

**Non-Goals**: exposing the destructive bootstrap import scripts; SSO/OAuth; Alembic;
touching LRMIS production; multi-node horizontal scaling of the API.

## Decisions

### D1. In-process services, not subprocesses

Each `cmd_*` body in `src/pipeline.py` is extracted into a plain function
(`services` layer) taking kwargs and returning a dict / raising typed exceptions;
`cmd_*` becomes a thin argparse adapter. The API imports these directly — structured
results, shared connection pools, no stdout scraping. Same treatment applies to
`scripts/integration_admin.py` logic (already importable) and `worker.process_once`
(already importable).

### D2. FastAPI + SSE

FastAPI with uvicorn. Reads (status, queues, entities, drift) are plain GET endpoints
the UI polls at 3–5s. Job progress is pushed over Server-Sent Events
(`GET /api/jobs/{id}/events`, plus a firehose `GET /api/events`) — SSE over WebSocket
because the browser only ever receives; actions go through normal POSTs.

### D3. Durable jobs: `admin_job` + `admin_job_event`

State machine: `queued → running → succeeded | failed | cancelled`. Columns include
`job_type`, `params JSONB`, `requested_by`, `reason`, `progress_current/total`,
`heartbeat_at`, `result JSONB`. Execution: an in-process runner thread pool claims
`queued` rows with `FOR UPDATE SKIP LOCKED` (same idiom as the outbox), emits
`admin_job_event` rows (which also feed SSE), and heartbeats; a reaper marks
stale-heartbeat `running` jobs `failed` so a crashed process never shows "running"
forever. Job types are a fixed allowlist mapping to service functions: `schema_scan`,
`discover`, `propose`, `deploy`, `backfill`, `reconcile`, `monitor`, `refresh`,
`worker_run`, `replay`, `entity_toggle`, `migration_apply`. The UI can never submit a
shell command.

The delivery worker loop is a special standing job: start/stop toggle backed by a
`threading.Event`, reusing `worker.process_once` per iteration.

### D4. Concurrency guards live in Postgres

- Deploy: `pg_try_advisory_lock(hashtext('deploy:' || proposal_id))` + status re-check
  inside the transaction; new `deploying` intermediate status so `deployed` is only set
  after MySQL DDL + trigger creation both succeed. Second concurrent deploy gets HTTP
  409, never a blocking wait.
- Migration apply: one fixed advisory lock key; checksum comparison before execution.
- Replay: keep the existing compare-and-swap; map `rowcount == 0` to HTTP 409.
- Job-level duplicate guard: before enqueueing deploy/refresh/migration jobs, reject if
  a `running` job targets the same scope (UX signal; the advisory lock remains the
  correctness guarantee).

### D5. Migration tracking, home-grown

`integration.schema_migrations(filename PK, checksum, applied_at, applied_by, success)`.
Apply: advisory lock → sha256 of file vs any recorded checksum (mismatch ⇒ hard fail:
an already-applied file was edited) → run whole file in one transaction (Postgres DDL is
transactional) → record row after commit. `sql/001_integration_foundation.sql` gains
`IF NOT EXISTS` so the Docker init-mount path and the tracker path can coexist. MySQL
staging DDL stays on the existing programmatic `_create_staging_table` path — a
deliberate scope boundary.

### D6. Auth and audit

`integration.admin_user(username PK, password_hash, role, is_active)` with bcrypt;
signed session cookie (`itsdangerous`); roles `operator` (workflows) and `admin`
(migrations + user management). Audit: `integration.admin_action_audit(actor, action,
target_type, target_id, request_id, reason, details JSONB, result, error_message,
performed_at)` written by one dependency/decorator wrapping every mutating route, in the
same transaction as the mutation. The audited actor always comes from the session —
client-supplied `--by`-style fields are gone from the API surface.

### D7. Guarded one-click contract

Every mutating action carries the authenticated actor automatically. Risk tiers:

- **One click** (reason optional): schema scan, discover, status reads, single replay,
  single kill-switch toggle, worker single pass.
- **Confirm modal** (reason required): deploy of a new entity, backfill, reconcile-fix
  actions, worker loop start/stop, schema approval.
- **Typed confirmation** (type the entity/file name + reason): refresh, redeploy of an
  already-deployed entity, migration apply, bulk kill-switch disable. Refresh/redeploy
  additionally snapshot the staging table (`RENAME TO <table>_bak_<ts>`, keep last 2)
  before dropping, with a restore-from-snapshot action.

### D8. Frontend

Vite + React + TypeScript in `web/`, React Router, TanStack Query for polling reads,
native `EventSource` for SSE. Dev proxy to the API; production build served by FastAPI
as static files (single deployable). Database-console visual language: dark slate
theme, monospace identifiers, status chips using the outbox/entity enums, left nav of
entities, guarded action modals. No component framework dependency beyond headless
primitives; styling via CSS modules or Tailwind (implementer's choice, one of the two,
consistently).

## Risks / Trade-offs

- **In-process job runner dies with the API process** — mitigated by durable job rows +
  heartbeat reaper; jobs are re-runnable/idempotent by construction. Accepted for a
  single-host internal tool; the SKIP LOCKED claim design permits moving the runner to
  a separate process later without schema changes.
- **Session auth without SSO** — acceptable on a private network; revisit if exposed
  beyond it.
- **Two dashboards of truth during rollout** (CLI still works) — intentional; CLI and
  UI share the same service functions and audit path, so state cannot diverge.

## Rollout

1. SQL migration + service extraction land first (CLI-compatible, independently
   testable).
2. API with read-only endpoints → dashboards usable without any mutation risk.
3. Mutating endpoints with guards + audit.
4. Frontend pages in dependency order (Overview → Worker & Queues → Schema Scanner →
   Onboarding/Mapping Review → Migrations → Audit Log).
