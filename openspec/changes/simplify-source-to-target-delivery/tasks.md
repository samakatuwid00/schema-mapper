## 1. Prove the single direct path (non-destructive)

- [ ] 1.1 Inventory current entities and their delivery route: run `python scripts/lrmis_cutover.py` and record which are legacy-staging vs. already on `lrmis_target`.
- [ ] 1.2 For each legacy entity, re-propose against the LRMIS target schema, resolve mappings, and `deploy_to_lrmis` so `onboarding_entity.lrmis_target_tables` is set (this is the bounded local-test migration, not the deferred 75-entity production one).
- [ ] 1.3 Run the mapping validation gate (`src/services/lrmis_mapping.py`) for every entity; fix or quarantine-route any coverage / unknown-column / unsatisfiable-FK failures before proceeding.
- [ ] 1.4 In a branch, run the worker with only the direct route enabled and verify: target row counts match source, quarantine is empty, and re-running a batch is idempotent (no duplicate `external_reference` rows).

## 2. Collapse the worker to one delivery path

- [ ] 2.1 Remove `_deliver_legacy` and the `is_path_b_entity` branch in `src/worker.py`; every claimed event goes through `_deliver_path_b` / `lrmis_delivery.deliver_event`.
- [ ] 2.2 Remove the staging connector from the delivery loop (`MySQLStagingConnector` staging role); keep only the target connector (`MySQLStagingConnector.for_target()` / the `lrmis_target` pool in `src/admin_api/db.py`).
- [ ] 2.3 Simplify `process_once` / `run_loop` signatures now that no `staging` argument is needed; update callers.
- [ ] 2.4 Update `src/lrmis_delivery.py` docstrings/naming so it reads as the only path (drop "NEW path / legacy entities keep..." framing).
- [ ] 2.5 Update `tests/test_worker_routing.py` and `tests/test_lrmis_delivery.py`: delete legacy-route assertions, assert single-path behavior, per-event atomicity, and quarantine isolation.

## 3. Nightly rebuild service

- [x] 3.1 Add a `nightly_refresh` service (e.g. `src/services/nightly_refresh.py`) that composes: restore source → reset target → re-deliver all entities, returning a structured per-entity result (delivered/quarantined counts, timings).
- [x] 3.2 Implement the guarded source-restore step: typed target-name/DSN confirmation + reason, advisory lock (same pattern as deploy/refresh), audit record, and abort-before-target-mutation on failure. *(Guard: opt-in `restore`, refuses to guess a destructive command, aborts before reset on failure; CLI `--confirm <db>`; job is admin-only + single-flight + audited at enqueue. The concrete restore shell command is env-configurable — pending confirmation of your dump format.)*
- [x] 3.3 Implement the target reset: `SET FOREIGN_KEY_CHECKS=0`, TRUNCATE all pipeline-written tables, reseed the FK-closure lookup set from `src/lrmis_registry.py`, then re-enable FK checks before delivery. *(Delegated to the verified `recreate_target_database()`, which drops+recreates the target with only the FK-closure lookups seeded and FK checks toggled — exactly the "truncate, reseed lookups" model.)*
- [x] 3.4 Implement the pre-reset backup (timestamped `mysqldump` of the target) and record its path in the run result.
- [x] 3.5 Implement dry-run mode: report source and target row counts and make no mutation.
- [x] 3.6 Add `scripts/nightly_refresh.py` entry point (on-demand + schedulable) that calls the service; document the source dump format and restore command it expects.
- [x] 3.7 Tests: `tests/test_nightly_refresh.py` covering dry-run-changes-nothing, failed-restore-leaves-target-untouched, ordering, restore-not-configured, and continue-past-a-failing-entity (9 tests, all green).

## 4. Job orchestration + observability

- [x] 4.1 Add `nightly_refresh` to the job-type allowlist and register its concurrency scope; ensure overlapping rebuilds return HTTP 409. *(Also gated admin-only.)*
- [x] 4.5 Expose `deploy_lrmis` as an admin job (handler wrapping `deploy_to_lrmis` + confirm-modal reason tier + single-flight scope) so entities can be migrated to the target path from the UI/API, not just Python. *(This closes the gap that left 0/124 entities on the target path.)*
- [x] 4.6 Add `bulk_deploy_lrmis` job + `bulk_deploy_to_lrmis` service (deploy every approved, not-yet-migrated entity in one pass, continue past failures, per-entity results); single-flight scope. Add the `nightly_refresh` typed-confirmation ("REBUILD") router tier. Backend-tested (continue-past-failure).
- [x] 4.7 Add `bulk_propose_lrmis` job + service (one Gemini call per deployed not-yet-migrated entity → fresh LRMIS-target proposals; continue past failures; reason-gated, single-flight). Backend-tested.
- [x] 4.11 **Staging-mapping resilience:** `_load_proposal_mappings` (deploy) and `load_entity_mappings` (delivery) now ignore any leftover `irimsv_*_staging` mappings, so a proposal with mixed old-staging + new-LRMIS mappings deploys cleanly (fixed the `schools` deploy failure). Verified: `schools` deployed to station/station_address/station_head/station_logo/station_name/station_type and delivers with 0 quarantine.
- [x] 4.12 **Direct Source→Target row comparison** (data-browser): new `compare_source_target` service + `GET /api/data/compare-source-target?entity=&pk=` (keyed on the source PK, derives the deterministic external_reference, follows the crosswalk to the exact target rows, compares per mapping); Data Browser's source-side "Compare" now opens a Source→Target modal (replacing the obsolete source↔staging one). Verified end-to-end on real `authors` data.
- [x] 4.9 **Manual multi-table mapping** (for tables the AI can't auto-map, e.g. `schools`): `resolve` accepts a `target_table` (sets `suggested_target_table`); new `GET /api/lrmis-schema` lists LRMIS tables→columns; the review UI (`MappingLanes`) gains LRMIS table + column pickers, enabled for both pending and rejected fields. Component-tested (resolve sends target_table).
- [x] 4.10 **Honesty fixes for the ready-to-deploy list:** `list_proposals` returns `has_lrmis_mapping`; the picker dedupes to the latest proposal per entity and shows only those with a real LRMIS mapping (was 354 rows for 61 entities → the stale-staging deploy failures are gone); `_deployable_proposals` likewise requires a real LRMIS mapping. Rejected the `customer` dummy + pruned duplicate `schools` proposals.
- [x] 4.8 **Pilot fixes (found by migrating `authors` end-to-end):** (a) scope `lrmis_delivery.load_entity_mappings` to the entity's *latest* proposal so stale legacy-staging mappings no longer leak into delivery and quarantine the event; (b) stop the UI gating Deploy on the proposal's whole-schema `unmet_required_columns` (deploy_to_lrmis is the real coverage gate). Verified: `authors` deploys + delivers real rows into `lrmis_target.author`.
- [ ] 4.2 Remove staging-cleanup job types (`sweep_staging`, `retire_entity`) from the allowlist/registration and reject them.
- [ ] 4.3 Repoint schema scanning to source ↔ `lrmis_target` only: replace `observe_staging_to_target` in `src/services/scan.py`, drop staging fingerprint/observe paths, keep target DDL drift via `src/services/lrmis_schema.py`.
- [ ] 4.4 Update observability tests to assert no `lrmis_staging` fingerprint or drift report is produced.

## 5. Delete staging (destructive removal — only after sections 1–4 are green)

- [ ] 5.1 Enumerate every reference to the staging/views path: `VIEWS_DATABASE`, `is_views_table`, `_qt` routing, `view_proposer`, and the `MySQLStagingConnector` staging role across the ~9 modules noted in `docs/PATH_B.md`.
- [ ] 5.2 Delete `src/services/staging_cleanup.py` and its tests; remove the `sweep_orphans` / `retire_entity` service functions and API routes.
- [ ] 5.3 Remove the staging views/`_qt` routing and the `view_proposer` feature (or repoint any still-needed piece to the target); delete dead helpers in `src/connectors.py`.
- [ ] 5.4 Remove `lrmis_staging` / `lrmis_staging_views` from `docker-compose.yml` and the staging keys from `.env` / `.env.example`; document dropping the databases.
- [ ] 5.5 Run the full `pytest` suite and delete or update every test that asserted staging behavior; fix any import breakage from removed modules.
- [ ] 5.6 Run `graphify update .` after removals so the graph no longer surfaces deleted staging nodes.

## 6. UI: unify language, remove staging, add rebuild control

- [ ] 6.1 Remove the staging data browser and any staging-specific pages/actions from `web/`.
- [ ] 6.2 Replace "Migration"(DDL)/"Backfill"(data) collision in the delivery context with uniform "delivery" language; keep central-DDL "Migrations" page as-is (unchanged capability).
- [ ] 6.3 Add the nightly-rebuild control with typed-confirmation modal (states "source will be restored and target reset") and a status panel showing in-progress state and last-run timestamp/actor/per-entity counts.
- [ ] 6.4 Update destructive-confirmation dialogs to display the current **target** row count (not staging).
- [ ] 6.5 Frontend tests for the rebuild control (confirmation gating, in-progress display, last-run summary).
- [x] 6.6 Add a "Deploy to target" button to Mapping Review — enqueues `deploy_lrmis`, gated on approved + no unmet columns, with a confirm modal and live status. Component-tested (4 tests: enabled/disabled gating + enqueues the job with proposal_id + reason).
- [x] 6.7 Rework the Mapping Review picker: add an "Approved — ready to deploy to target" section (per-row Deploy + a bulk "Deploy all N" button) using a new `on_target` field on `list_proposals`, so approved entities are reachable without knowing URLs. Component-tested.
- [x] 6.8 Add a Nightly Rebuild page (Maintain nav) — Dry run + typed-confirmation ("REBUILD") Run, optional source-restore, and a last-run status/result panel wired to `nightly_refresh`. Component-tested (dry-run + typed-confirm gating).

## 7. Validate and close out

- [ ] 7.1 End-to-end on the live stack: restore a fresh source dump, run the nightly rebuild, confirm target counts vs source and empty quarantine; verify the target is a clean deliverable copy central office can append to.
- [ ] 7.2 `openspec validate simplify-source-to-target-delivery --strict` passes.
- [ ] 7.3 Update `docs/PATH_B.md` to reflect that Phases 8–9 are done via this change (single path, staging removed).
- [ ] 7.4 Mark the superseded changes: close/withdraw `improve-staging-cleanup` and the staging half of `three-way-schema-observability` so they are not implemented in conflict.
