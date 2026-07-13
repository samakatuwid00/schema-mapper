## Context

The integration currently runs two delivery paths side by side:

- **Legacy staging** (`worker._deliver_legacy`): one source row → one flat `irimsv_*_staging` MySQL table of VARCHARs in the `lrmis_staging` database. No relational structure, no FK, purely a landing zone.
- **Path B / direct** (`worker._deliver_path_b` → `lrmis_delivery.deliver_event` → `lrmis_writer.write_source_row`): one source row fans out across the real 51-table LRMIS schema in `lrmis_target`, parents-first, propagating auto/app-assigned parent ids, upserting on the immutable `external_reference` via the crosswalk. Opt-in per entity when `onboarding_entity.lrmis_target_tables` is set. Guarded by a mapping validation gate (coverage / unknown table-column / unsatisfiable FK) and per-event commit/rollback with quarantine on failure.

Path B already answers the operator's core worry — a source table maps to a differently-named target table, or splits across several target tables — because mappings are per-column `source_column → target_table.target_column`, produced by the Gemini draft + human resolve step. Staging adds a hop, a second vocabulary ("Migration"=DDL vs "Backfill"=data), and a second code path, for no capability the target path lacks.

Deployment reality (confirmed with the operator): this is **local testing**; the target database is a *deliverable copy* that central office takes and appends to. The operator wants each midnight to (1) drop/restore the fresh `lrmis` source dump into central and (2) reset the target to a clean, fully-current copy — one command, easy to run.

## Goals / Non-Goals

**Goals:**
- Exactly one delivery path: direct source → LRMIS target, driven by the AI-approved multi-table mappings.
- Delete staging entirely — code, jobs, views/`_qt` routing, and the `lrmis_staging` / `lrmis_staging_views` databases.
- One guarded, audited **nightly rebuild** command: restore source → TRUNCATE pipeline target tables → reseed FK-closure lookups → re-deliver all current source rows.
- Simpler operator surface: uniform "delivery" language, staging pages gone, a dedicated nightly-rebuild control and status.

**Non-Goals:**
- The generic multi-dialect migration engine (`generic-ai-db-migration-engine`) — out of scope.
- Any change to central-Postgres DDL migration file management (`migration-management` stays as-is).
- Target-side row editing, or a swap/zero-downtime target (the target may be empty during the midnight rebuild window — acceptable for this use case).
- Rewriting the AI mapping/resolve UX beyond removing the staging/naming confusion (the separate `database-manager-ui-simplification` change owns the guided-workflow redesign).

## Decisions

### D1 — Direct-to-target is the only path; staging is deleted, not kept as a fallback
The worker collapses to a single route: load the entity's approved mappings, `deliver_event`, commit/rollback, quarantine on failure. `_deliver_legacy`, the staging connector role, and `is_path_b_entity` gating are removed (every entity is "Path B" now).
- **Alternative considered:** keep staging dormant behind a flag as a fallback. **Rejected** — it preserves the exact dual-path confusion this change exists to remove, and the operator confirmed local testing so there is no production fallback to protect.
- **Consequence:** the ~9 modules and `view_proposer` that still reference the views/staging path (per `docs/PATH_B.md`) must be deleted or repointed; this is the bulk of the removal work and is enumerated in tasks.

### D2 — Nightly target reset = TRUNCATE data tables + reseed FK-closure lookups
The reset TRUNCATEs every pipeline-written target table, then reseeds only the FK-closure lookup set (`psgc` + the ~8 other reference tables the source does not carry, already defined as the seed set in `lrmis_registry`), then re-delivers all current source rows.
- **Why not scoped crosswalk delete** (Path B's `refresh_entity` default)? The operator wants a *clean, deterministic* target copy each night, not an incremental one; TRUNCATE + full re-deliver is simpler to reason about and cannot leave stale rows from a prior mapping.
- **Why not "truncate everything, reseed nothing"** (the operator's first instinct)? FK-dependent inserts (e.g. a `school` row referencing a `psgc` code) fail at load time if the referenced lookup rows do not exist. Reseeding just the FK-closure guarantees integrity without depending on when central office appends their data. Data tables are still fully fresh each night.
- **MySQL mechanics:** TRUNCATE on an FK-referenced table errors. The reset runs inside a `SET FOREIGN_KEY_CHECKS=0` window for the truncate + reseed, then **re-enables checks before delivery** so real deliveries still enforce referential integrity. Truncate order is not load-bearing while checks are off, but reseed happens before FK-enabled delivery begins.

### D3 — One orchestrated nightly job, with the destructive source restore as an explicit guarded step
A single `nightly_refresh` job/script composes: (a) restore the fresh `lrmis` dump into central (`pg_restore`/`psql` of the operator-provided dump), (b) target reset per D2, (c) full re-delivery of every onboarded entity's current source rows. Step (a) is destructive (it drops/recreates the source `irimsv` data) and therefore requires typed confirmation of the target DSN + a reason, takes the same advisory-lock pattern used by deploy/refresh/migration-apply, and writes an audit record. The job is idempotent and re-runnable: source is the authority, so a re-run rebuilds the same target.
- **Alternative considered:** leave the source restore to an external DBA cron, job only does central→target. **Rejected** — the operator chose "orchestrate the whole run" for fewest manual steps.
- **Sequencing safety:** delivery (c) only starts after (a) and (b) succeed; a failure in (a) aborts before any target mutation.

### D4 — Migrate the local entities to direct delivery as part of this change
Because staging is deleted, every currently-deployed local entity must have `lrmis_target_tables` set and an approved target mapping before its first direct delivery. For local testing this is a bounded, scripted step (re-propose against the LRMIS target schema → resolve → `deploy_to_lrmis`), not the incremental 75-entity production migration deferred in `docs/PATH_B.md`.

### D5 — Observability collapses to source ↔ target
`scan.observe_staging_to_target` and the staging fingerprint/observe paths are removed; schema scanning fingerprints source (Postgres `irimsv`) and target (`lrmis_target` MySQL) only. Target DDL drift (already built in `lrmis_schema.check_ddl_drift`) is the tracked second side and continues to pause impacted entities on breaking drift.

## Risks / Trade-offs

- **The midnight source restore is irreversible and TRUNCATE is not transactional in MySQL** → require typed DSN confirmation + reason + advisory lock + audit; take a target snapshot/`mysqldump` immediately before the reset; rely on source being the authority so any failed run is fixed by re-running. Never point the restore at anything but the configured central/target DSNs.
- **Target is empty between TRUNCATE and the end of re-delivery** → schedule at midnight (off-hours), and have the nightly control clearly show "rebuild in progress" so no one reads a half-filled target. Zero-downtime swap is explicitly a non-goal.
- **FK-closure seed can drift from LRMIS's real reference data** → seed from the versioned `lrmis_registry` seed set and surface target-DDL/lookup drift via D5 so a lookup change is noticed, not silently wrong.
- **Removing staging breaks the ~9 modules + `view_proposer`** → enumerate every `VIEWS_DATABASE` / `is_views_table` / `_qt` / staging-connector reference before deleting; run the full `pytest` suite; delete their tests too rather than leaving dead assertions.
- **Supersedes in-flight changes** → `improve-staging-cleanup` (cleans a thing we now delete) and the staging half of `three-way-schema-observability` become moot; call this out so they are archived/closed rather than implemented in conflict.

## Migration Plan

1. **Prove the single path first (non-destructive):** set `lrmis_target_tables` + approved target mappings for the local entities (D4); run the worker with only the direct route in a branch; verify target counts vs source and quarantine is empty.
2. **Land the nightly rebuild** (`nightly_refresh` service + script + job-type) against the already-onboarded entities; dry-run (counts only) before the first real TRUNCATE; verify reseed + re-delivery reproduces the same target.
3. **Delete staging** once (1)–(2) are green: remove `_deliver_legacy`, staging connector role, views/`_qt` routing, `staging_cleanup.py`, staging pages in `web/`, `lrmis_staging*` from `docker-compose.yml`/`.env`; drop the `lrmis_staging` / `lrmis_staging_views` databases; delete their tests.
4. **UI + language:** remove staging browser/pages, unify on "delivery," add the nightly-rebuild control + status.
5. **Rollback:** before step 3, staging code is still present on the prior commit; revert the removal commit to restore the dual path. After step 3, rollback is git-revert of this change plus re-creating the staging DBs from `docker-compose`.

## Open Questions

- **Scheduler mechanism** for "midnight": in-app loop/APScheduler vs OS cron / Task Scheduler invoking `scripts/nightly_refresh.py`? Leaning to a plain script + OS scheduler for simplicity, with the job also runnable on-demand from the UI. To confirm during apply.
- **Target pre-reset backup**: `mysqldump` to a timestamped file vs the existing snapshot mechanism — confirm which is least friction on the operator's Windows/Docker setup.
- **Exact source-restore command** (`pg_restore` custom-format vs `psql` of a plain SQL dump) depends on how the operator produces the `lrmis` dump today; confirm the dump format during apply.
