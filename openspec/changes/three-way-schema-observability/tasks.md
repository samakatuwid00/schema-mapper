## 1. Migration — three-way drift tagging

- [x] 1.1 Create `sql/010_three_way_drift.sql` adding `drift_pair TEXT NOT NULL DEFAULT 'source->staging'` to `integration.schema_drift_report`
- [x] 1.2 Add an index on `(drift_pair, created_at DESC)`
- [x] 1.3 Register the migration in `src/services/migrations.py` MIGRATION_FILES
- [x] 1.4 Path B contract fingerprint reuses the existing `schema_version` scope columns (`scope_kind='contract', scope_name='target_b'`) — no new table

## 2. Data Browser — third side (`src/services/data_browser.py`)

- [x] 2.1 In `list_browsable_tables`, open `MySQLStagingConnector.for_target()` and add a `path_b` key with tables from `lrmis_target`; annotate each with its matching `staging_table` (entity lookup then prefix fallback)
- [x] 2.2 Extend `fetch_rows` to accept `side="path_b"` and read from `lrmis_target` (reuse existing identifier allowlisting)
- [x] 2.3 Implement `compare_staging_target(staging_table, primary_key_value, ...)` — field-by-field PK comparison of a staging row vs its Path B canonical row, returning match flags
- [x] 2.4 Return `path_b_table` annotations on staging rows so the UI can offer a staging↔target comparison

## 3. Schema observability — second drift pair (`src/services/scan.py`, `ops.py`)

- [x] 3.1 In `ops.get_schema_trees`, add a `target_b` tree = fingerprint of `lrmis_target` via `for_target()`; keep `source` and `staging`
- [x] 3.2 Add `observe_staging_to_target(...)` in `scan.py` — fingerprint the Path B contract (`scope_name='target_b'`) and diff it against the `lrmis_staging` contract
- [x] 3.3 Add `record_drift(..., drift_pair)` argument and thread `drift_pair` into the `schema_drift_report` insert
- [x] 3.4 Extend `scan(..., mode="source"|"staging")` to run the source pair (default) or the staging→target pair, returning both trees

## 4. API — endpoints (`src/admin_api/routers.py`)

- [x] 4.1 `GET /api/data/tables` returns the new `path_b` key
- [x] 4.2 `GET /api/data/rows?side=path_b` handled by `fetch_rows`
- [x] 4.3 Add `GET /api/data/compare-staging-target?staging_table=&pk=` (read-only, audited `data_browse`) → `compare_staging_target`
- [x] 4.4 `GET /api/schemas` returns `source` + `staging` + `target_b` trees
- [x] 4.5 Drift reports endpoint carries `drift_pair` for UI filtering

## 5. Job handler — `schema_scan` mode (`src/admin_api/jobs.py`, `routers.py`)

- [x] 5.1 `_h_schema_scan` passes `mode` from params into `scan()`
- [x] 5.2 `schema_scan` remains in the one-click/job allowlist; `mode` is an optional non-destructive param

## 6. Web UI — Data Browser (`web/src/pages/DataBrowser.tsx`)

- [x] 6.1 Segmented control becomes three options: Source DB / Staging DB (`lrmis_staging`) / Target DB (`lrmis_target`)
- [x] 6.2 Rail + row grid support the `path_b` side
- [x] 6.3 Compare button rules: Source→Staging (by `external_reference`), Staging↔Target (by PK), Source↔Target (Path B) never offered

## 7. Web UI — Schema Changes stepper (`web/src/pages/SchemaChanges.tsx`)

- [x] 7.1 Replace the static two-tree layout with a 3-step stepper: Source → Staging → Target (Path B)
- [x] 7.2 Corner next/back arrows between Staging and Target (Path B) cards, reversible
- [x] 7.3 Scan panel relabels: Source step "Source schema" (`mode=source`); Staging step "Staging schema" (manager types staging table, `mode=staging`); Target step read-only summary
- [x] 7.4 Drift reports list filters by `drift_pair` per active step

## 8. Tests

- [x] 8.1 Unit: `list_browsable_tables` includes `path_b` tables with staging annotations
- [x] 8.2 Unit: `fetch_rows(side="path_b")` reads from `lrmis_target` (mocked connector)
- [x] 8.3 Unit: `compare_staging_target` flags matching/diverging fields
- [x] 8.4 Unit: `scan(mode="staging")` records a `staging->target` drift report
- [x] 8.5 Integration: full flow with mocked MySQL/PostgreSQL returning three consistent schemas
- [x] 8.6 Frontend: 3-toggle comparison rules (source↔target disabled, staging↔target enabled)
- [x] 8.7 Frontend: stepper next/back navigation relabels the scan input
- [x] 8.8 Run full suite: `pytest -q` and `npm run build`
