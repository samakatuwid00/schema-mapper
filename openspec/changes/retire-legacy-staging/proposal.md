## Why

The pipeline still carries a second, now-redundant delivery path: the legacy
single-table MySQL **staging** route (`irimsv_*_staging` tables in `lrmis_staging`,
plus the `lrmis_staging_views` projection DB). It exists only for entities not yet
onboarded to direct delivery. The generic engine
(`generic-ai-db-migration-engine`) has since built and **live-proven** the
replacement — the dialect-aware `GenericWriter` delivering source rows straight
into the real target (MySQL *or* Postgres), with FK propagation, crosswalk
idempotency, and the AI mapping/agent on top. Keeping staging alive now costs
more than it earns: a duplicated code path, two extra databases, staging-only
cleanup jobs, and a vocabulary/UI split that confuses operators.

This change is the **destructive cutover**: migrate every remaining legacy entity
to direct delivery, then delete the staging path and its databases. It is the
step `docs/PATH_B.md` Phases 8–9 and `simplify-source-to-target-delivery`
deferred, now unblocked because the direct path is complete and verified. It also
makes the deferred generic-engine ports (`3.4 _infer_column_type`,
`4.5 generate_refresh_sql`, `4.6 target_fetch_sql`) moot — those are **deleted**,
not ported.

## What Changes

- **Prerequisite gate:** every `status='deployed'` entity is migrated to direct
  delivery (its `lrmis_target_tables` footprint is set) and verified, so no entity
  depends on staging. The cutover refuses to run while any legacy-only entity
  remains.
- **BREAKING — single delivery path.** The worker's `_deliver_legacy` route and
  the `is_path_b_entity` fork are removed; every event delivers through the direct
  `deliver_event`/`GenericWriter` path.
- **BREAKING — staging code deleted:** `src/services/staging_cleanup.py`
  (`sweep_orphans`/`retire_entity`), `pipeline._create_staging_table` /
  `_infer_column_type`, `fast_refresh.generate_refresh_sql` /
  `drop_staging_table`, `snapshots.snapshot_staging_table`, and the
  `MySQLStagingConnector` staging/views surface (`VIEWS_DATABASE`, `_qt`,
  `is_views_table`) — the connector keeps only its target role (used by
  `MySQLTargetAdapter`).
- **BREAKING — databases dropped:** `lrmis_staging` and `lrmis_staging_views`.
- **Jobs:** the `sweep_staging` and `retire_entity` job types are removed from the
  allowlist; scanning/drift stop covering staging.
- **UI:** the staging data browser/pages are removed; data movement is uniformly
  "delivery".
- **Tests/docs:** delete `tests/test_staging_cleanup.py`; update
  `docs/PATH_B.md` (Phases 8–9 → done) and the graph.

## Capabilities

### New Capabilities
- `single-delivery-path`: The pipeline has exactly one delivery path — source rows
  fan out directly into the real target via `GenericWriter` (dialect-aware,
  FK-propagating, crosswalk-idempotent). No `irimsv_*_staging` tables, no
  `lrmis_staging`/`lrmis_staging_views` databases, no legacy route.

### Modified Capabilities
- `job-orchestration`: the job-type allowlist drops `sweep_staging` and
  `retire_entity`; no delivery route enqueues staging work.
- `schema-observability`: schema scanning/drift no longer include the staging
  databases; observation is source ↔ real-target only.
- `admin-dashboard`: the staging data browser and staging-specific pages/actions
  are removed; the UI presents a single "delivery" concept.

## Impact

- **Prerequisite:** run to completion only after the migration gate passes for all
  deployed entities (reuse `schema_swap`/`deploy_to_lrmis` + `redeliver_all`).
- **Delete/trim:** `src/worker.py` (`_deliver_legacy`), `src/services/staging_cleanup.py`,
  `src/services/snapshots.py` (staging snapshot), `src/fast_refresh.py`
  (`generate_refresh_sql`/`drop_staging_table`), `src/pipeline.py`
  (`_create_staging_table`/`_infer_column_type`), `src/connectors.py`
  (staging/views surface of `MySQLStagingConnector`), `src/admin_api/db.py`
  (`staging()` accessor), `src/admin_api/jobs.py` (staging job handlers), `web/`
  (staging pages), `tests/test_staging_cleanup.py`.
- **DB:** drop `lrmis_staging` / `lrmis_staging_views`; remove their `.env`/compose
  entries.
- **Relationship to other changes:** completes the deferred cutover of
  `simplify-source-to-target-delivery` (and supersedes its staging-deletion tasks)
  and `docs/PATH_B.md` Phases 8–9; depends on `generic-ai-db-migration-engine`
  (done). The pending `improve-staging-cleanup` change is obsolete (staging is
  deleted, not cleaned).
- **Rollback:** this is destructive and irreversible against the databases; the
  code deletions are revertable via git, but the dropped databases are not. Take a
  backup and run behind the migration gate.
- **Out of scope:** the generic engine itself, target-side row editing, and the
  central Postgres DDL migration runner (`migration-management` unchanged).
