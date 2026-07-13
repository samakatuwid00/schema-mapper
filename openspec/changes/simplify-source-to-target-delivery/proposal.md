## Why

Today the pipeline moves data in two hops — source → MySQL `lrmis_staging` → (eventually) the real LRMIS target — and it routes each entity down one of two code paths (legacy single-table staging vs. the newer direct "Path B"). That split is the root of the confusion the operator hits: an unclear multi-step sequence, a "resolve all" step whose effect is ambiguous, and a vocabulary collision where "Migration" means *DDL* and "Backfill" means *data*. The staging hop earns none of this cost — it is a flat VARCHAR landing table that performs no transformation the target path cannot. The whole reason the project has an AI mapping step is to absorb the case-by-case reality that a source table like `users` maps to a differently-named target table, or splits across several target tables; that logic already lives in the direct path and does not need staging.

This change finishes the already-running "Path B" work: make **direct source → target** the *only* delivery path, delete staging, and add a **single guarded nightly rebuild** so the whole midnight refresh is one command instead of a manual sequence. Simpler mental model, fewer moving parts, one place to look.

## What Changes

- **BREAKING: Direct-to-target becomes the only delivery path.** The worker stops routing to `lrmis_staging`; every approved entity's rows fan out through its AI-approved multi-table mappings into the real LRMIS target tables (the existing `deliver_event` / `write_source_row` path), with the same validation gate, per-event transactional commit/rollback, quarantine, and `external_reference` crosswalk upsert.
- **BREAKING: The legacy staging path is removed** — the single-table `_deliver_legacy` route, the `MySQLStagingConnector` staging role, the `irimsv_*_staging` tables/views (`VIEWS_DATABASE` / `is_views_table` / `_qt` routing), `lrmis_staging` cleanup jobs (`sweep_orphans` / `retire_entity`), and the `lrmis_staging` / `lrmis_staging_views` databases. Since this is local testing, the handful of local entities are migrated to direct delivery as part of the change rather than left on a dormant fallback.
- **New one-command nightly rebuild.** An orchestrated, audited job restores the fresh `lrmis` source dump into central, then rebuilds the target: **TRUNCATE all pipeline-written target tables, reseed only the FK-closure lookups** the source does not carry (`psgc` + the ~8 other reference tables), then re-deliver every current source row. The destructive source-restore step is an explicit, confirmed, advisory-lock-guarded operation.
- **Unified language and slimmer UI.** With one path and one destination, the dashboard drops the staging browser/pages and the Migration-vs-Backfill collision: data movement is uniformly "delivery" and the nightly rebuild has its own clearly-labeled control and status.

## Capabilities

### New Capabilities

- `direct-target-delivery`: The single, direct delivery path — an approved source row is transformed by its AI-approved per-column mappings and fanned out across the real LRMIS target tables (parents-first, FK propagation, app-assigned ids, crosswalk upsert), gated by mapping validation and isolating bad rows to quarantine. No staging hop exists.
- `nightly-refresh`: A single orchestrated, guarded, audited nightly operation — restore the fresh source dump, truncate pipeline-written target tables, reseed the FK-closure lookups, and re-deliver all current source rows — idempotent and safe to re-run.

### Modified Capabilities

- `job-orchestration`: The job-type allowlist gains `nightly_refresh` (with its own concurrency scope) and drops the staging-only jobs (`sweep_staging`, `retire_entity`); the delivery worker no longer has a staging route.
- `schema-observability`: Schema scanning and drift become **source ↔ target** only — staging is removed from scanning, fingerprinting, and drift reports; target-schema (DDL) drift on `lrmis_target` is the tracked second side.
- `admin-dashboard`: The staging data browser and staging-specific pages/actions are removed; data movement is presented uniformly as "delivery"; the nightly rebuild gets a dedicated, plainly-labeled control and status panel.

## Impact

- **Delivery/worker:** `src/worker.py` (drop `_deliver_legacy` + staging routing; single path), `src/lrmis_delivery.py` (delivery + refresh, becomes the only path), `src/connectors.py` (retire the staging connector role; keep the target connector).
- **Nightly rebuild:** new orchestration entry (e.g. `scripts/nightly_refresh.py` + a `nightly_refresh` service/job) composing source restore → `TRUNCATE` + reseed FK-closure → full re-delivery; reuses `lrmis_registry` seed set and `refresh_entity`.
- **Schema/DDL:** `src/services/lrmis_schema.py` (target DDL fingerprint/drift stays), `src/services/scan.py` (`observe_staging_to_target` → source↔target), removal of staging fingerprint/observe paths.
- **Staging removal:** delete `src/services/staging_cleanup.py`, staging views/`_qt` routing, `lrmis_staging*` from `docker-compose.yml`, `.env` staging keys; drop the `lrmis_staging` / `lrmis_staging_views` databases.
- **Web:** remove staging browser/pages and Migration/Backfill naming collision in `web/`; add nightly-rebuild control + status.
- **Docs/superseded work:** `docs/PATH_B.md` Phases 8–9 become this change; the pending `improve-staging-cleanup` change is superseded (staging is deleted, not cleaned); `three-way-schema-observability` collapses to two-way.
- **Out of scope:** the generic multi-dialect engine (`generic-ai-db-migration-engine`), any target-side row editing, and central Postgres DDL migration file management (`migration-management` is unchanged — the nightly *source restore* is orchestrated by `nightly-refresh`, not the SQL migration runner).
