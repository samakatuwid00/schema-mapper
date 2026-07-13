## Why

There are **three** databases in the pipeline, but the admin UI only surfaces two:

1. **Source** — IRIMSV (PostgreSQL `irimsv` schema) — the ingested system.
2. **Path A / Staging** — `lrmis_staging` (MySQL) — per-entity delivery buffers in the LRMIS shape.
3. **Path B / Target** — `lrmis_target` (MySQL) — the 51 canonical LRMIS tables created by `scripts/init_lrmis_target.py`.

Today the Data Browser and Schema Changes pages only show Source and Path A. Operators cannot inspect Path B, cannot compare Path A (staging) against Path B (target), and cannot detect schema drift between the staging contract and the canonical target — a gap that hides the exact failure mode the user is now asking about ("if the LRMIS target schema changed, does staging change?"). We need a three-way view with clear comparison rules.

## What Changes

- **Data Browser gains a third toggle** — "Target DB (lrmis_target)" — alongside the existing Source DB and Staging DB, all read-only and audited.
- **Comparison rules enforced by design**:
  - Source ↔ Staging: compared by `external_reference` (existing behavior).
  - Staging ↔ Target (Path B): compared by primary key (both share the LRMIS column layout).
  - Source ↔ Target (Path B): **never offered** — different schema design (IRIMSV vs LRMIS).
- **Schema Changes becomes a 3-step stepper** — Source → Staging → Target (Path B) — with next/back arrows in the corner.
- **Scan input relabels per step**: on the Source step it reads "Source schema" (default `irimsv`, mode `source`); on the Staging step it reads "Staging schema" (manager types the staging table name, mode `staging`) and the scan verifies the staging↔target (Path B) contract; the manager can hop back to Source and vice versa.
- **Two distinct drift pairs** recorded and reported: `source->staging` (existing) and `staging->target` (new), filterable on each step.

## Capabilities

### Modified Capabilities
- `schema-observability`: gains a second comparison pair — the staging (Path A) contract vs the canonical target (Path B) contract — adding a `staging` scan mode and a `target_b` schema tree to the existing source/target observation.
- `data-browser`: gains a third read-only side (`lrmis_target`) and a staging↔target row comparison by primary key; the source↔target comparison remains impossible by design.
- `admin-dashboard`: the Schema Changes surface becomes a three-step navigable stepper with a relabeling scan control.

### New Capabilities
- `three-way-schema-drift`: detection and reporting of schema drift specifically between Path A (staging) and Path B (canonical target), independent of source→staging drift.

## Impact

- **Backend service**: `src/services/data_browser.py` (add `path_b` tables/rows + `compare_staging_target`); `src/services/scan.py` (add `observe_staging_to_target`, `mode` param to `scan`); `src/services/ops.py` (`get_schema_trees` returns `staging` + `target_b`).
- **API**: `/api/data/tables` and `/api/data/rows` gain the `path_b` side; new read-only `/api/data/compare-staging-target`; `/api/schemas` returns three trees; `/api/schemas/drift` (drift reports) carries `drift_pair`.
- **Migration**: `sql/010_three_way_drift.sql` adds `drift_pair TEXT` to `integration.schema_drift_report` (and reuses the existing `schema_version` scope columns for the Path B contract).
- **Web UI**: `web/src/pages/DataBrowser.tsx` (3 toggles + comparison rules); `web/src/pages/SchemaChanges.tsx` (3-step stepper + relabeling scan).
- **Tests**: backend unit/integration for the new path_b listing, row fetch, staging↔target compare, and the staging scan mode; frontend tests for the 3-toggle rules and the stepper.
