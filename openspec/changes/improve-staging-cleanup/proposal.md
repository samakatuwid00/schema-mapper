## Why

The existing staging-table cleanup (`sweep_orphans` / `retire_entity`) was built ad-hoc and has two problems: (1) after a sweep job completes, the UI displays only "Cleanup job complete — job #N" with zero visibility into which orphans were found, which were dropped, or what snapshots were taken — so the user can't verify the sweep actually did anything; (2) "Retire entity" requires the operator to look up a bare numeric entity ID (the `onboarding_entity.id` PK) from the database or devtools, which is error-prone and slow.

## What Changes

- **Sweep job result display**: After a sweep completes, show the full result: list of orphan tables found, which were dropped, and which snapshots were created.
- **Retire entity result display**: After a retire completes, show which entity was retired, its source/staging table, whether it was dropped, and its snapshot name.
- **Entity dropdown for Retire**: Replace the bare numeric Entity ID text input with a `<select>` dropdown of all deployed entities showing `source_table` → `staging_table` (with status). Selecting one submits its `id`.
- **Investigate and fix any logic gaps** in `sweep_orphans` that might prevent it from actually detecting/dropping orphan tables (e.g., the function queries column-level info just to derive table names, and dry-run defaults to True).
- **Named connectors fix**: `MySQLStagingConnector` lacks a `close()` method; the `finally` block in `sweep_orphans` only closes `central`. Add symmetry for staging connector cleanup.

## Capabilities

### New Capabilities
- `staging-cleanup`: Staging table lifecycle management — retire entity (drop staging table + disable) and sweep orphan staging tables (drop unreferenced staging tables), with snapshot-before-drop safety.

### Modified Capabilities
- `admin-dashboard`: Schema Changes page gains result visibility for cleanup jobs and a deployed-entity dropdown for the Retire action.
- `job-orchestration`: Extend the documented allowlist and scope registration to include `retire_entity` and `sweep_staging`.
- `schema-observability`: (Optional) If a 3-way drift-detection gap is discovered during investigation, address it here.

## Impact

- **staging_cleanup.py**: Fix `sweep_orphans` to use a direct table-name query instead of `information_schema.columns`; add `close()` support for symmetry; add diagnostics logging.
- **connectors.py**: Add `close()` method to `MySQLStagingConnector`.
- **SchemaChanges.tsx**: Replace Entity ID `<input>` with a `<select>` populated from `/api/status` entities; add result renderers for sweep/retire job results.
- **admin_api/status.py** (if not already): Ensure `/api/status` returns the entity list with `id`, `source_table`, `staging_table`, `status` for the dropdown.
- **Tests**: Update `test_staging_cleanup.py` for any logic changes; add tests for result display.
