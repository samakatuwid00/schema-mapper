## 0. Migration gate (no destruction until this passes)

- [x] 0.1 Precheck (`src/services/cutover.py`): lists legacy-only entities (`lrmis_target_tables IS NULL`) across ALL statuses, classified by status — corrected from a `status='deployed'`-only filter (0 are 'deployed'; they're paused/discovered/etc.)
- [ ] 0.2 Migrate each BLOCKING legacy entity — `propose` → `deploy_to_lrmis` → `redeliver_all`; or disable if retired. OPERATIONAL: 25 entities blocking (24 paused + 1 reviewed), live gate run 2026-07-13
- [x] 0.3 Gate: `ready` iff no legacy-only entity is in a delivering/resumable state (`deployed/paused/reviewed`); `disabled`/`discovered` are terminal. Pure `summarize()` unit-tested
- [x] 0.4 `scripts/cutover.py --precheck` — read-only, repeatable, exits 3 when BLOCKED (CI-gateable); `--json`. LIVE: BLOCKED, 25 entities

## 1. Collapse the worker to one delivery path

- [ ] 1.1 Remove `_deliver_legacy` from `src/worker.py`
- [ ] 1.2 Remove the `is_path_b_entity` fork in `process_once`; deliver every event via `deliver_event`
- [ ] 1.3 Remove staging-only helpers used solely by the legacy route (`approved_mapping`, staging `transform_row` glue, `save_projection` if staging-only)
- [ ] 1.4 Update `tests/test_worker_routing.py` — one path; add a MySQL-target + Postgres-target delivery smoke

## 2. Delete staging code

- [ ] 2.1 Delete `src/services/staging_cleanup.py` (`sweep_orphans`, `retire_entity`, `_active_staging_tables`, `_is_staging_table`) + `tests/test_staging_cleanup.py`
- [ ] 2.2 Delete `pipeline._create_staging_table` + `pipeline._infer_column_type` (generic-engine 3.4 becomes deletion)
- [ ] 2.3 Delete `fast_refresh.generate_refresh_sql` + `drop_staging_table` (4.5 → deletion); keep any non-staging refresh helpers
- [ ] 2.4 Delete `snapshots.snapshot_staging_table` + its `_qt`
- [ ] 2.5 Trim `MySQLStagingConnector`: remove `VIEWS_DATABASE`, `is_views_table`, `_qt` views routing, and staging read helpers (4.6 → deletion) — KEEP `upsert`/`bulk_insert`/`information_schema`/`connection` (target role)
- [ ] 2.6 Remove `staging()` accessor from `src/admin_api/db.py`

## 3. Jobs, observability, UI

- [ ] 3.1 Remove `sweep_staging` / `retire_entity` from the job-type allowlist + their handlers in `src/admin_api/jobs.py`
- [ ] 3.2 Remove the staging databases from schema scanning/drift (`services/scan.py`, `services/ops.py`) — observe source ↔ real target only
- [ ] 3.3 Remove the staging data browser + staging-specific pages/actions from `web/`; unify on "delivery"
- [ ] 3.4 Grep the codebase for any remaining `lrmis_staging` / `_staging` / `VIEWS_DATABASE` references and clean them

## 4. Drop databases (destructive, last)

- [ ] 4.1 Backup `lrmis_staging` + `lrmis_staging_views` (mysqldump)
- [ ] 4.2 Drop both databases behind a typed-confirmation flag (mirror `nightly_refresh --confirm <db>`)
- [ ] 4.3 Remove `LRMIS_STAGING_*` (staging-role) + `VIEWS_DATABASE` from `.env.example` and `docker-compose.yml`

## 5. Verify + document

- [ ] 5.1 Full `pytest` green; MySQL-target and Postgres-target delivery smokes pass
- [ ] 5.2 `docs/PATH_B.md` — mark Phases 8–9 done; note `simplify-source-to-target-delivery` staging tasks + `improve-staging-cleanup` are superseded
- [ ] 5.3 `graphify update .`; `openspec validate retire-legacy-staging --strict`
