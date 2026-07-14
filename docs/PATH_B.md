# Path B — direct delivery into the real LRMIS schema

Path B replaces the single VARCHAR `irimsv_*_staging` tables with direct writes
into the canonical 51-table LRMIS schema (a parallel `lrmis_target` database).
One source row fans out across several LRMIS tables with foreign keys
propagated from auto-generated / app-assigned parent ids.

It is built **parallel and opt-in**: an entity uses Path B only once its
`onboarding_entity.lrmis_target_tables` footprint is set. Every legacy entity
carries `NULL` there and keeps its original `lrmis_staging` delivery path
untouched.

## What exists (Phases 0–7, done)

| Piece | Where |
|---|---|
| LRMIS schema registry (FK graph, topo sort, defaults, seed set) | `src/lrmis_registry.py` |
| Multi-table writer (parents-first, FK propagation, app-assigned station id, crosswalk upsert, scoped delete) | `src/lrmis_writer.py` |
| Target database create + seed + delivery_audit | `scripts/init_lrmis_target.py` |
| Mapping validation gate (coverage, unknown table/column, unsatisfiable FK) | `src/services/lrmis_mapping.py` |
| Delivery + refresh into `lrmis_target` | `src/lrmis_delivery.py` |
| Worker routing (Path B vs legacy, gated) | `src/worker.py` |
| Deploy an entity to Path B | `src/services/lrmis_onboarding.py` |
| Target DDL fingerprint + drift + redeploy plan | `src/services/lrmis_schema.py` |
| Cutover status (read-only) | `scripts/lrmis_cutover.py` |

Migrations `sql/005`–`008`: crosswalk PK widened to include `target_table`;
`id_sequence` for app-assigned station ids; `onboarding_entity.lrmis_target_tables`;
`target_ddl` schema-version scope.

### Key decisions (verified against real data)

- `station` and `psgc` are the only tables without an AUTO_INCREMENT PK.
  `psgc` (national geographic codes IRIMSV does not carry) stays read-only,
  seeded. `station` is **app-assigned** — 0 of 145 IRIMSV schools match an
  existing LRMIS station, so resolve-only would block onboarding; new stations
  get ids from a reserved range (>= 10,000,000; LRMIS's own ids are 1..3921).
- Refresh never TRUNCATEs — it deletes only pipeline-written rows via the
  crosswalk, so LRMIS-owned and seeded reference rows survive.
- Seeded lookups are the FK-closure of the write set (9 tables), not every
  table with seed data — the multi-million-row library catalog stays empty.

## Setup

```bash
python scripts/init_lrmis_target.py          # create + seed lrmis_target (idempotent)
# onboard an entity to Path B (after discover/propose/review):
#   src.services.lrmis_onboarding.deploy_to_lrmis(proposal_id, by)
python scripts/lrmis_cutover.py              # status
```

`.env`: keep `LRMIS_STAGING_SSL_DISABLED=false` — MySQL 8.4's
caching_sha2_password cannot authenticate over an unencrypted connection.

## Phases 8–9 — done via `retire-legacy-staging`

The final cutover is carried by the `retire-legacy-staging` OpenSpec change:

- **Phase 9 (migrate entities)** — DONE. Its migration gate (`scripts/cutover.py
  --precheck`) migrated the real legacy-only entities to the direct LRMIS target
  and disabled the artifacts/dupes; the gate now passes (0 blocking).
- **Phase 8 (remove staging code)** — DONE. The worker collapsed to one delivery
  path; the staging DDL/refresh/snapshot/cleanup code, the interactive `onboard`
  CLI, `fast_refresh`, and `ops.reconcile` were deleted; the connector's views
  routing (`VIEWS_DATABASE`, `is_views_table`, `_qt`) was removed; the drift/
  monitor/scan machinery and the data browser now observe **source ↔ the real
  `lrmis_target`** only (three-way collapsed to two-way).
- **Drop the databases** — PREPPED, NOT RUN. `scripts/drop_legacy_staging.py`
  backs up then drops `lrmis_staging` / `lrmis_staging_views` behind an exact
  typed `--confirm`. Run it deliberately, with the backup, then apply the §4.3
  compose/.env edits. NOTE: that MySQL container also hosts `lrmis_target` — drop
  the databases, not the container, and keep `LRMIS_STAGING_HOST/PORT/USER/PASSWORD`.

**Operational note (fingerprint rebaseline):** the drift monitor's *target*
fingerprint now describes the real `lrmis_target` tables instead of the old
staging table. After deploying/migrating an entity, run
`python scripts/rebaseline_entity_fingerprints.py --by <you> --apply` once, or the
next `monitor`/`schema_scan` will flag a one-time spurious target-drift.

Superseded: the staging tasks in `simplify-source-to-target-delivery` and the
whole `improve-staging-cleanup` change are obsolete — staging is being retired.
