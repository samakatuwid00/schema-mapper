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

## Remaining (Phases 8–9, deferred — destructive)

Not done, on purpose. They are the final cutover and are irreversible against a
system with 75 live entities:

- **Phase 9** — migrate each legacy entity: re-propose against the LRMIS target
  schema, deploy_to_lrmis, backfill/refresh, verify counts, retire the old
  `irimsv_*_staging` table.
- **Phase 8** — only once every entity is migrated: remove the views/staging
  code paths (`VIEWS_DATABASE`, `is_views_table`, `_qt` routing, snapshots view
  logic — still used by 9 modules and the active `view_proposer` feature) and
  drop `lrmis_staging` / `lrmis_staging_views`.

Run these deliberately, one entity at a time, with backups.
