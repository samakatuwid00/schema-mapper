# Tasks: Add Admin Database Dashboard

## 1. Foundations (SQL + service extraction)

- [x] 1.1 Add `IF NOT EXISTS` / guarded enum creation to `sql/001_integration_foundation.sql`
- [x] 1.2 Create `sql/003_admin_ui.sql`: `admin_user`, `admin_action_audit`, `admin_job`, `admin_job_event`, `schema_migrations` tables; mount in `docker-compose.yml`
- [x] 1.3 Extract `src/pipeline.py` command bodies into `src/services/` functions returning dicts / raising typed exceptions (discover, propose, review, resolve, deploy, backfill, reconcile, monitor, refresh, status); keep `cmd_*` as thin CLI wrappers
- [x] 1.4 Add advisory lock + status re-check + `deploying` intermediate status to the deploy service
- [x] 1.5 Add staging-table snapshot (`RENAME TO <table>_bak_<ts>`, keep last 2) + restore helper used by refresh/redeploy
- [x] 1.6 Make `worker` loop controllable via `threading.Event`; expose `run_once` and loop status
- [x] 1.7 Verify `pytest -q` and existing CLI commands still pass unchanged

## 2. Backend API core

- [x] 2.1 Add fastapi/uvicorn/sse-starlette/passlib/itsdangerous to `requirements.txt`; new env vars in `.env.example`
- [x] 2.2 Scaffold `src/admin_api/` (app factory, settings, router registration, error-to-HTTP mapping)
- [x] 2.3 Auth: login/logout, signed session cookie, bcrypt verification, role dependency (`operator`/`admin`), user management endpoints (admin role), bootstrap first admin via CLI helper
- [x] 2.4 Audit middleware/dependency writing `admin_action_audit` in-transaction on every mutating route
- [x] 2.5 Read endpoints: outbox stats, entities, entity control, proposals + field reviews, quarantine, dead-letter, drift reports, audit log (paginated/filterable)

## 3. Job orchestration

- [x] 3.1 Job store: enqueue (allowlist-validated), claim via `FOR UPDATE SKIP LOCKED`, heartbeat, progress, terminal states; `admin_job_event` emission
- [x] 3.2 In-process runner thread pool + stale-heartbeat reaper
- [x] 3.3 Map allowlisted job types to service functions; scope-conflict 409 checks for deploy/refresh/migration
- [x] 3.4 SSE endpoints: per-job event stream + global firehose
- [x] 3.5 Worker loop start/stop/status endpoints wired to the controllable worker

## 4. Schema observability

- [x] 4.1 Scan service combining source+target fingerprint/diff primitives, returning structured diff and enumerating paused entities
- [x] 4.2 Scan job type + drift report read endpoints
- [x] 4.3 Schema tree endpoints (source schema, staging schema via information_schema)

## 5. Migration management

- [x] 5.1 Migration runner service: ordered file list, checksum verify, advisory lock, single-transaction apply, tracker row
- [x] 5.2 Endpoints: list applied/pending with checksums, apply (admin role + typed confirmation contract), SQL preview
- [x] 5.3 Backfill tracker rows for Docker-initialized databases (mark-as-applied action with audit)

## 6. Frontend

- [x] 6.1 Scaffold `web/` (Vite + React + TS, Router, TanStack Query, EventSource client, dev proxy, FastAPI static serving of production build)
- [x] 6.2 App shell: dark database-console theme, entity sidebar, status chips, guarded action modal components (one-click / confirm+reason / typed-confirmation tiers)
- [x] 6.3 Login page + session handling
- [x] 6.4 Overview page (live health cards, drift alerts)
- [x] 6.5 Worker & Queues page (loop toggle, outbox table, quarantine/dead-letter inspection, replay)
- [x] 6.6 Schema Scanner page (scan trigger, schema trees, diff viewer)
- [x] 6.7 Onboarding Wizard (discover → propose → review/resolve → deploy → backfill with job progress)
- [x] 6.8 Mapping Review page (mapping lanes, resolve actions, unmet required columns)
- [x] 6.9 Migrations page (applied/pending, SQL preview, typed-confirmation apply)
- [x] 6.10 Drift Reports + Audit Log pages

## 7. Tests & verification

- [x] 7.1 Backend tests: auth/roles, audit-in-transaction, job allowlist + state machine, deploy conflict (409), migration checksum mismatch, replay CAS mapping
- [x] 7.2 Frontend tests: guarded modal tiers, status chip rendering, job drawer updates from SSE events
- [ ] 7.3 E2E happy path against Docker stack (DEFERRED — carried into database-manager-ui-simplification verification)
- [x] 7.4 `pytest -q` and `python -m compileall -q src scripts tests` clean

## 8. Docs & context refresh

- [x] 8.1 Update README/LOCAL_SETUP: admin UI quickstart; correct stale script references to `python -m src.pipeline` equivalents
- [x] 8.2 Update `CLAUDE.md` with admin UI dev commands
- [x] 8.3 Run `graphify update .` so the graph includes the new backend/frontend
- [x] 8.4 `openspec validate add-admin-database-dashboard --strict` passes; archive change after acceptance
