## 1. Connector — lifecycle symmetry + efficient table listing

- [x] 1.1 Add a no-op `close()` method to `MySQLStagingConnector` (interface symmetry with `PostgresCentralConnector`)
- [x] 1.2 Add a `table_names()` helper to `MySQLStagingConnector` that queries `information_schema.tables` (one row per table) for the connector's database

## 2. Staging cleanup — logic fixes (`src/services/staging_cleanup.py`)

- [x] 2.1 `sweep_orphans`: derive table names via `staging.table_names()` instead of scanning `information_schema.columns`
- [x] 2.2 `sweep_orphans`: close the staging connector in `finally` when it was created here (`owns_staging`)
- [x] 2.3 `retire_entity`: close the staging connector in `finally` when it was created here (`owns_staging`)

## 3. Web UI — Retire dropdown + cleanup result display (`web/src/pages/SchemaChanges.tsx`)

- [x] 3.1 Replace the bare "Entity ID" text input with a `<select>` of deployed entities from `/api/status` (label `source_table → staging_table`, value `id`)
- [x] 3.2 Render the sweep job result: orphans found, dropped, and snapshots (capped at 20 with a "… and N more" overflow line)
- [x] 3.3 Render the retire job result: entity, `source_table → staging_table`, dropped flag, and snapshot name

## 4. Tests

- [x] 4.1 Update `tests/test_staging_cleanup.py` so the fake staging connector exposes `table_names()`; keep the orphan-detection assertions passing
- [x] 4.2 Add a test that `sweep_orphans`/`retire_entity` close a connector they own (staging `close()` symmetry)
- [x] 4.3 Frontend: the Retire control renders a deployed-entity dropdown and a completed cleanup job shows its structured result
- [x] 4.4 Run full suite: `pytest -q` and `npm run build`
