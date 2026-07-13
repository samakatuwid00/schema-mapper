# Design — Three-way schema observability

## Overview

The pipeline has three MySQL/PostgreSQL stores. Today only two are observable. This
change makes the third (Path B `lrmis_target`) browseable and introduces a
**second, independent drift pair**: Path A (`lrmis_staging`) contract vs Path B
(`lrmis_target`) canonical contract.

```
 IRIMSV (PostgreSQL)  ──scan mode=source──▶  lrmis_staging (Path A, MySQL)
                                                     │  ▲
                                            scan mode=staging (compares by PK)
                                                     ▼  │
                                              lrmis_target (Path B, MySQL)
```

The `MySQLStagingConnector.for_target()` classmethod **already exists** and points
at `lrmis_target` via `LRMIS_STAGING_*`-style env vars (host/port/user/password
overridden to the target DB). No new connector is required — reuse it.

## 1. Data Browser — third side

### `src/services/data_browser.py`

- `list_browsable_tables(...) -> dict` adds a **`path_b`** key alongside the
  existing `source` and `target`:
  - Opens `MySQLStagingConnector.for_target()` and lists `information_schema`
    tables (excluding `delivery_audit` and MySQL internals).
  - For each Path B table, annotate `staging_table` = the matching Path A
    staging table. Mapping rule (priority order):
    1. `integration.onboarding_entity` row whose canonical `target_table` (from
       `integration.mapping_version.target_table`) equals the Path B table name →
       use its `staging_table`.
    2. Fallback: strip a `staging_` prefix / add one (`staging_parcels` ↔
       `parcels`).
  - Returns `{source, target, path_b}` with per-table
    `{table, columns, rows, staging_table, path_b_table}` annotations.

- `fetch_rows(side, table, ...)` gains `side="path_b"`:
  - Opens `for_target()` connector and reads the page from `lrmis_target`.
  - Source stays PostgreSQL; `target` stays `lrmis_staging`. Identifier
    allowlisting (table + sort column checked against live `information_schema`)
    already in place is reused for the new side.

- **New `compare_staging_target(staging_table, primary_key_value, ...)`**:
  - Reads one row from `lrmis_staging` and the matching row from `lrmis_target`
    (resolved via the same mapping rule).
  - Both sides share the LRMIS column layout, so compare **field by field by
    primary key** and return `{fields:[{field, staging, target, matches,
    compared}], missing_in_target, missing_in_staging}`.
  - Mirrors the existing `compare_row` (source↔staging by `external_reference`)
    but matches on PK instead.

### Comparison rules (enforced in UI, documented in spec)

| Selected side | Can compare to | Match key |
|---------------|----------------|-----------|
| Source        | Staging only   | `external_reference` (existing) |
| Staging       | Source **or** Target (Path B) | ref for source, PK for target |
| Target (Path B) | Staging only | PK |
| **Source ↔ Target (Path B)** | **never** | different schema design |

## 2. Schema Changes — 3-step stepper

### `src/services/ops.py` — `get_schema_trees`

Returns **three** trees:
- `source` — IRIMSV fingerprint (unchanged).
- `staging` — `lrmis_staging` contract fingerprint (today's "target").
- `target_b` — **new**: `lrmis_target` contract fingerprint via
  `for_target()` and `from_information_schema(..., "LRMIS_B")`.

### `src/services/scan.py`

- `observe_target` already fingerprints the `lrmis_staging` contract under
  `scope_kind='contract', scope_name=''`. Add a sibling:
  `observe_staging_to_target(...)` fingerprints `lrmis_target` under
  `scope_kind='contract', scope_name='target_b'` and diffs it against the
  `lrmis_staging` contract, recording **`staging->target`** drift.
- `scan(..., mode="source"|"staging")`:
  - `mode="source"` (default) → existing behavior (IRIMSV vs staging), drift
    pair `source->staging`.
  - `mode="staging"` → runs `observe_staging_to_target`, drift pair
    `staging->target`.
  - Returns both trees so the UI can render whichever step is active.

### Drift storage — `sql/010_three_way_drift.sql`

- `ALTER TABLE integration.schema_drift_report ADD COLUMN drift_pair TEXT NOT NULL DEFAULT 'source->staging';`
- Index on `(drift_pair, created_at DESC)` for fast filtering.
- The Path B contract fingerprint is stored in the **existing** `schema_version`
  table (already scope-aware from `sql/008_target_ddl_scope.sql`) under
  `target_system='LRMIS', scope_kind='contract', scope_name='target_b'` — no new
  table needed.
- `record_drift` gains a `drift_pair` argument threaded through to the insert.

### API — `src/admin_api/routers.py` / `jobs.py`

- `GET /api/data/tables` → includes `path_b`.
- `GET /api/data/rows?side=path_b&...` → handled by `fetch_rows`.
- `GET /api/data/compare-staging-target?staging_table=&pk=` → new read-only,
  audited (`data_browse`) endpoint → `compare_staging_target`.
- `GET /api/schemas` → returns `source` + `staging` + `target_b`.
- `GET /api/schemas/drift` → each report carries `drift_pair`.
- `schema_scan` job handler passes `mode` from params into `scan()`.

## 3. Frontend

### `web/src/pages/DataBrowser.tsx`

- Segmented control becomes **three** options: `Source DB` / `Staging DB`
  (`lrmis_staging`) / `Target DB` (`lrmis_target`).
- Rail + rows support the `path_b` side.
- `Compare` button logic:
  - Hidden when Source or Path B is selected (no valid counterpart on the
    disallowed axis) — actually kept only when a counterpart exists per the table
    above. Source shows compare iff it has a `staging_table`; Staging shows
    compare to source OR to Path B; Path B shows compare to staging.

### `web/src/pages/SchemaChanges.tsx`

- Replace the static two-column tree layout with a **3-step stepper**:
  `Source → Staging → Target (Path B)`.
- Each step card shows one `SchemaTree` and a corner arrow:
  - Staging card → right arrow reveals the Target (Path B) card.
  - Target (Path B) card → left arrow returns to Staging (and vice versa).
- The top **Scan** panel relabels by active step:
  - Source step → label "Source schema" (default `irimsv`),
    `scan({mode:"source"})`.
  - Staging step → label "Staging schema" (text input where the manager types
    the staging table name to inspect), `scan({mode:"staging"})`; on success the
    UI shows whether the staging↔target (Path B) contract drifted.
  - Target (Path B) step → read-only contract summary (end of stepper).
- Drift reports list filters by `drift_pair`: Staging step shows
  `source->staging`; Target (Path B) step shows `staging->target`.

## Risks / Decisions

- **Staging↔target table-name mapping** is the only fuzzy part; the entity
  lookup (mapping `target_table` → `staging_table`) is authoritative and the
  prefix fallback covers the 51 canonical tables.
- **Read-only guarantee** for the new side is preserved by reusing the existing
  identifier allowlisting and `Cache-Control: no-store` + audit path — no new
  write surface is introduced.
- Source↔Target (Path B) comparison is intentionally omitted to avoid
  meaningless diffs between IRIMSV and LRMIS shapes.
