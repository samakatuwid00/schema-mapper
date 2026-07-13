## Context

Two delivery paths coexist in `src/worker.py`:

- **legacy staging** (`_deliver_legacy`) — transforms a source row into one flat
  `irimsv_*_staging` VARCHAR table in `lrmis_staging`, with a `lrmis_staging_views`
  projection DB and staging-only cleanup (`sweep_orphans`, `retire_entity`);
- **direct** (`_deliver_path_b` → `deliver_event` → `GenericWriter`) — fans the
  row into the real target tables.

`is_path_b_entity()` routes per entity. The direct path is now complete and
live-verified (bulk 1459 rows into a real Postgres target, idempotent). This
design removes the legacy path safely.

## Goals / Non-Goals

**Goals**
- Zero entity left depending on staging before any deletion (a hard gate).
- Collapse the worker to a single delivery path.
- Delete the staging code, jobs, UI, and databases.
- Keep the `MySQLStagingConnector` **target** role intact (used by
  `MySQLTargetAdapter`); only its staging/views surface goes.

**Non-Goals**
- No change to the direct/generic delivery behaviour itself.
- No new target features.
- Not a data migration tool — entities are migrated with the existing
  discover→propose→deploy→redeliver flow, not bespoke scripts.

## Decisions

### D1 — Migration gate before any destruction
A read-only precheck lists every `status='deployed'` entity whose
`lrmis_target_tables IS NULL` (still legacy-only). The cutover **aborts** unless
that list is empty. Migration itself reuses the proven flow: `propose` →
`deploy_to_lrmis` (sets the target footprint) → `redeliver_all`. This is the same
gate `docs/PATH_B.md` Phase 9 describes, now scriptable.

### D2 — Single worker path
Remove `_deliver_legacy` and the `is_path_b_entity` branch; `process_once`
delivers every claimed event through `deliver_event` (which already selects the
target engine + writer via `_open_target`). `approved_mapping`/`transform_row`
staging helpers used only by the legacy route are removed with it.

### D3 — Trim the connector, don't delete it
`MySQLStagingConnector` is also the MySQL **target** connector
(`for_target()` → `MySQLTargetAdapter`). Delete only the staging/views surface:
`VIEWS_DATABASE`, `is_views_table`, the `_qt` views-routing, and the staging
read helpers. Keep `upsert`/`bulk_insert`/`information_schema`/`connection`.

### D4 — Delete, don't port, the staging DDL/refresh helpers
`pipeline._infer_column_type`/`_create_staging_table`,
`fast_refresh.generate_refresh_sql`/`drop_staging_table`, and
`snapshots.snapshot_staging_table` exist only to build/refresh staging tables.
The generic engine's `dialect.generic_to_ddl` + `GenericWriter` are their
replacement, so these are deleted (this is why generic-engine tasks 3.4/4.5/4.6
were deferred rather than ported).

### D5 — Drop databases last, behind explicit confirmation
Dropping `lrmis_staging`/`lrmis_staging_views` is irreversible. Gate it behind a
typed-confirmation flag (mirroring `nightly_refresh --confirm <db>`) and a
pre-drop backup, run only after the code is on the single path and tests are
green.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| An un-migrated entity silently loses its delivery path | Hard gate (D1): cutover aborts while any legacy-only deployed entity exists. |
| Removing the connector breaks the MySQL target | D3 keeps the target role; only staging/views surface removed; full suite + a MySQL-target smoke gate the change. |
| Irreversible database drop | D5: typed confirmation + backup; drop is the last step, separable from the code change. |
| Hidden staging consumers (UI, jobs, tests) | Enumerate via the graph; remove jobs from the allowlist and staging pages from `web/`; delete `test_staging_cleanup.py`. |

## Cutover sequence

1. **Gate** — precheck: no deployed entity with `lrmis_target_tables IS NULL`.
2. **Migrate** any remaining legacy entities (propose → deploy_to_lrmis →
   redeliver), re-run the gate.
3. **Code** — collapse the worker to the single path (D2), trim the connector
   (D3), delete the staging DDL/refresh/cleanup/snapshot helpers (D4), remove the
   staging jobs, remove the staging UI. Run `pytest` (delete
   `test_staging_cleanup.py`) + a MySQL-target and Postgres-target smoke.
4. **Databases** — backup, then drop `lrmis_staging`/`lrmis_staging_views` behind
   typed confirmation (D5); remove `.env`/compose entries.
5. **Docs/graph** — mark PATH_B Phases 8–9 done; `graphify update .`.

Rollback: steps 1–3 revert via git; step 4 requires restoring the databases from
backup.
