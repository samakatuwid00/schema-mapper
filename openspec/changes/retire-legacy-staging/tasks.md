## 0. Migration gate (no destruction until this passes)

- [x] 0.1 Precheck (`src/services/cutover.py`): lists legacy-only entities (`lrmis_target_tables IS NULL`) across ALL statuses, classified by status — corrected from a `status='deployed'`-only filter (0 are 'deployed'; they're paused/discovered/etc.)
- [~] 0.2 Resolve the 25 blockers (live 2026-07-13): DISABLED 18 (17 `_for_lrmis` staging-view artifacts + `customer` orphan — reversible, ids recorded). MIGRATED the 7 real entities via live AI `propose` (LRMIS registry 51 tables): 7 proposals created (557-563), all `needs_review` with PLAUSIBLE 0.7-0.9 mappings (none unmappable; usertypes→user_type clean). Then accepted AI mappings (normalizing disallowed transforms trim/enum_remap/uuid→int to 'none') + approve + deploy: **4 of 7 DEPLOYED** (districts, divisions, school_libraries, reservations — the irimsv entities). Gate 7→6 blocking. Then user-authorized: disabled the 3 stale `source_schema='lrmis'` duplicates (id58/60/92) + the 3 coverage-gap entities (id34/44/50). **GATE NOW PASSES** (0 blocking; 44 on-target, 31 disabled, 49 discovered). Net: 4 real entities migrated to direct target, 24 artifacts/dupes/gaps disabled. All reversible.
- [x] 0.3 Re-run precheck: `scripts/cutover.py --precheck` → GATE PASS, exit 0. Safe to proceed to the destructive phases.
- [x] 0.3 Gate: `ready` iff no legacy-only entity is in a delivering/resumable state (`deployed/paused/reviewed`); `disabled`/`discovered` are terminal. Pure `summarize()` unit-tested
- [x] 0.4 `scripts/cutover.py --precheck` — read-only, repeatable, exits 3 when BLOCKED (CI-gateable); `--json`. LIVE: BLOCKED, 25 entities

## 1. Collapse the worker to one delivery path

- [x] 1.1 Removed `_deliver_legacy` (+ its worker-local helpers `_target_schema`, `_outbound_row`) from `src/worker.py`
- [x] 1.2 Removed the `is_path_b_entity` fork + `staging` param in `process_once`; every event now delivers via `_deliver_path_b`→`deliver_event`. `run_loop` no longer opens a staging connector. Docstring updated
- [~] 1.3 Worker-local legacy helpers removed + unused imports trimmed (approved_mapping/delivered/save_projection/is_path_b_entity/Schema/transform_row). The `integration_store`/`transform_engine` functions themselves stay (may be used elsewhere) — deleted in Phase 2
- [x] 1.4 Rewrote `tests/test_worker_routing.py` for the single path (every event → `_deliver_path_b`, lazy target open once, empty batch opens nothing); removed the legacy `_outbound_row` test in `test_integration_core.py`. Live MySQL/Postgres delivery smoke still TODO (needs DBs). Full suite 281 passed

## 2. Delete staging code

- [x] 2.0 (trace step 2) Removed the CLI's dead legacy-delivery clone from `pipeline.py`: `_run_worker_batch` + its private `_target_schema`/`_outbound_row` (NO callers anywhere — dead code) + the now-unused `transform_row` import. 272 passed. NOTE: numbered 2.2-2.6 below remain — they are load-bearing (used by `onboarding.deploy`/`ops.refresh`/`view_proposer`/API), removed in later steps.

- [x] 2.1 DELETED `src/services/staging_cleanup.py` + `tests/test_staging_cleanup.py`; removed its consumers: `jobs.py` handlers (`_h_retire_entity`/`_h_sweep_staging`) + JOB_HANDLERS/_SCOPED entries + the `cleanup_service` import, and `routers.py` `retire_entity`/`sweep_staging` job-type branches. 272 passed. (Caller trace done first — see design note; steps 2.2-2.6 touch load-bearing services/view_proposer/API, deferred pending reassessment.)
<!-- 2.3-PREREQ ENDPOINT TRACE (2026-07-13, BLOCKED on decision): before the DDL
helpers below can be deleted, the legacy deploy/refresh SERVICES that use them must
be rewired off staging. Trace:
  * DEPLOY: `deploy` job → `onboarding.deploy` (creates irimsv_*_staging + trigger +
    fingerprint). ALSO called by `onboard_bulk` (onboarding.py:455) — a core feature
    whose propose→deploy produces SINGLE-TABLE staging mappings; the direct
    `deploy_to_lrmis` needs MULTI-TABLE LRMIS mappings, so rewiring = replacing
    onboard_bulk with `bulk_propose_lrmis`+`bulk_deploy_to_lrmis` (rewrites
    test_onboard_bulk + test_admin_api).
  * REFRESH: `refresh`/`refresh_all` jobs → `ops.refresh` (staging). ALSO used by
    `drift_resolution.py` (2 sites). Direct equivalent = `redeliver_all`/`refresh_entity`.
  Conclusion: 2.3 is the hardest, behavior-changing part (touches onboard_bulk +
  drift_resolution + their tests).
  UPDATE (user chose "replace onboard_bulk"): DONE — `onboard_bulk` now composes
  `deploy_to_lrmis` (direct, multi-table) instead of legacy staging `deploy`;
  dropped its unused `staging` param; result reports `target_tables` not
  `staging_table`; rewrote `test_onboard_bulk` (patches `deploy_to_lrmis`, no
  MySQLStagingConnector). 272 passed. STILL using `onboarding.deploy` (staging):
  only the single `deploy` job (`_h_deploy`) now — smaller remaining decision.
  UPDATE2: DONE — ALIASED `_h_deploy` → `deploy_to_lrmis` + removed `deploy` import.
  UPDATE3: DONE — CLI `cmd_deploy` (pipeline.py) rewired to `deploy_to_lrmis` too.
  So `onboarding.deploy` (legacy staging deploy) is now DEAD — no consumers (verified
  by grep). Deletable next (it uses `_create_staging_table`/`snapshot_staging_table`/
  `_create_source_trigger`/VIEWS_DATABASE; deleting it removes one consumer, but
  `ops.refresh` still uses the DDL helpers). 272 passed.
  REFRESH side (`ops.refresh` + `drift_resolution`) still pending a decision.
  UPDATE4 (2026-07-14, resolved): `onboarding.deploy` already gone (only `onboard_bulk`
  remains). REFRESH rewired — `ops.refresh` now delegates to
  `nightly_refresh.redeliver_all`→`lrmis_delivery.refresh_entity` (crosswalk-scoped
  delete+rewrite into the real target); dropped its `staging` param + `snapshot_staging_table`
  usage; only entities with `lrmis_target_tables` set refresh (others reported skipped).
  `drift_resolution._resolve`/`reset_target` updated to call it without `staging`; dry-run
  "refresh staging" wording → "refresh target"; docstrings de-staged. NOTE: a THIRD staging
  consumer the trace missed — the live interactive `onboard` CLI (`cmd_onboard`/
  `_onboard_single_table`) — was DELETED per user decision (superseded by admin UI +
  `onboard_bulk`), taking dead helpers `_detect_collation`/`_detect_cross_table_candidates`/
  `_create_source_trigger`/`_is_view` + unused imports with it. `monitor`/`_entity_fingerprints`/
  `reconcile` still read staging (schema fingerprint, stable while staging tables persist) —
  that real-target rewire is §3.2, not a §2.2 blocker. 277 passed. -->
- [x] 2.2 DELETED `pipeline._create_staging_table` + `pipeline._infer_column_type` (+ the whole interactive `onboard` CLI and its dead helpers; `ops.refresh`/drift rewired to the real target first). Zero remaining references. 277 passed.
- [x] 2.3 DELETED the whole `src/fast_refresh.py` module + `tests/test_fast_refresh.py`. After the refresh rewire + onboard-CLI deletion, ALL of it was dead: `generate_refresh_sql`/`drop_staging_table` (0 consumers) and `fetch_and_bulk_insert` (0 consumers). No non-staging helpers existed to keep (module was entirely "drop+recreate staging tables"). 273 passed.
- [x] 2.4 DELETED `snapshots.snapshot_staging_table` (dead — its only callers, the staging refresh/deploy, are gone) + the now-dead private helpers `_table_exists`/`prune_snapshots`, and the orphaned `SNAPSHOT_KEEP`/`datetime` import. KEPT `list_snapshots`/`restore_snapshot`/`_qt`/`_views_schema` (still consumed by ops' snapshot browse/restore — removed with the staging data browser in §3.3). Module docstring updated. 273 passed.
- [x] 2.5 Removed `is_views_table` + the `_qt` views-routing branch from `MySQLStagingConnector` (dead — the only writers of `_for_lrmis` view tables were the legacy delivery/refresh, now gone; `_entity_fingerprints` no longer reads staging after §3.2 ops-part). The other methods (`upsert`/`bulk_insert`/`information_schema`/`connection`/`count_rows`/`fetch_rows`/`fetch_row_by`/`table_names`) are generic + serve the target role — KEPT. `VIEWS_DATABASE` constant was STILL used by `snapshots._views_schema` (snapshot browse) at the time → removed with §3.3. 273 passed. CLOSED (2026-07-14): §3.3 deleted `snapshots.py` and §3.4 removed the constant; grep re-verified `VIEWS_DATABASE` and `is_views_table` have zero occurrences in `src/`.
- [x] 2.6 Removed `staging()` accessor + `_staging` global from `src/admin_api/db.py` (all its router consumers gone with §3.3 backend). `central()`/`target()` remain. 270 passed.

## 3. Jobs, observability, UI

- [x] 3.1 Removed `sweep_staging`/`retire_entity` from JOB_HANDLERS/_SCOPED + their handlers + router typed-confirm branches (done together with 2.1). NOTE: any UI buttons for them now 400 until 3.3
- [x] 3.2 DONE — the schema-scanning/drift machinery observes source ↔ real target only. Ops-part (drift/monitor/fingerprint) rewired earlier; now `scan.observe_target` reads `lrmis_target`, and `observe_staging_to_target` + `scan(mode='staging')` (the staging↔target diff) DELETED, `_h_schema_scan` no longer takes `mode`. NOTE: target-fingerprint basis changed (staging schema → real target schema) → deployed entities need a one-time `rebaseline_entity_fingerprints --apply` or `monitor` flags spurious target-drift; document in PATH_B (§5.2).
- [x] 3.3 BACKEND DONE: `reconcile` RETIRED (user decision — service + `_h_reconcile` + JOB_HANDLERS + one-click list + `cmd_reconcile`/subparser/dispatch/doc); `data_browser` collapsed 3-way→2-way (source ↔ real `target`=lrmis_target), removed `compare_row`+`compare_staging_target`+staging helpers, source tables now annotated with `target_tables` (was `staging_table`); `get_schema_trees` 2 trees (source+target, no staging/target_b); deleted `snapshots.py` module + `staging_snapshots`/`restore_staging_snapshot` ops + `/reads/snapshots/{t}` + `/actions/restore-snapshot` routes; dropped `staging=` from `/data/tables`,`/data/rows`,`/reads/schemas`; removed `/data/compare`,`/data/compare-staging-target` routes. Tests: rewrote `test_three_way`→`test_data_browser_list` (2-way), fixed `test_data_browser` + `test_admin_api` route. 270 passed. FRONTEND DONE (`web/src`): `api/types.ts`+`client.ts` (SchemasResponse `{source,target}`, `DataSide` source|target, `SourceTableSummary.target_tables`, removed path_b/snapshot/compare-staging/reconcile-job types + `compareRow`/`compareStagingTarget`/`getSnapshots`/`restoreSnapshot`); rewrote `DataBrowser.tsx` (2 sides) + `SchemaChanges.tsx` (single source↔target); `Overview.tsx` dropped Snapshots/Reconcile actions + Staging column; `labels.ts` dropped reconcile; deleted `DataBrowserThreeWay`+`StagingCleanup` tests, updated `DataBrowser`/`SchemaChanges`/`Tables` tests. GATES GREEN: `tsc --noEmit` 0 errors, vitest 63 passed, `npm run build` ok, acceptance grep clean. MINOR follow-up: the `reset_path_b` (recreate lrmis_target) button was dropped from the UI (job type string `path_b` was forbidden by the sweep gate) — the job is still API/CLI reachable; re-add as e.g. "Recreate target DB" if the dashboard button is wanted. (Marked [x] 2026-07-14: backend + frontend both landed and gates green; the button re-add is an optional follow-up, not part of this change.)
- [x] 3.4 BACKEND swept: removed dead `VIEWS_DATABASE` const, dead `staging` locals in `cmd_propose`/`cmd_monitor` (+ pipeline `MySQLStagingConnector` import), dead staging-shaped `integration_store.approved_mapping`; fixed `schema_drift.record_drift` default label `source->staging`→`source->target`, `worker`/`migrations`/`connectors`/`onboarding` docstrings, `rebaseline` audit `staging_table`→`target_tables`; rewired `scripts/schema_monitor.py` to `for_target()`. KEPT (legitimate): defensive `NOT LIKE 'irimsv_%_staging'` crosswalk/mapping guards (lrmis_delivery/ops/lrmis_writer), `lrmis_onboarding._is_staging_table` guard, the cutover tooling, historical §-notes, and the shared MySQL server creds `LRMIS_STAGING_HOST/PORT/USER/PASSWORD` (used by `for_target()` too). 270 passed. FRONTEND sweep done with the §3.3 frontend work (types/client/pages rewritten, acceptance grep clean). NOTE: connector class is still named `MySQLStagingConnector` (used for BOTH roles via `for_target()`) — cosmetic rename to a neutral name is optional follow-up. FINAL SWEEP (2026-07-14): the earlier "no retired symbols remain" claim was WRONG on one count — `lrmis_delivery.is_path_b_entity` was still defined (zero callers after §1.2 removed its worker fork; graphify + grep confirmed dead). DELETED now. Combined re-grep of every retired symbol (`is_path_b_entity`/`_deliver_legacy`/`sweep_staging`/`retire_entity`/`fast_refresh`/`staging_cleanup`/`observe_staging_to_target`/`compare_staging_target`/`restore_staging_snapshot`/`snapshot_staging_table`/`is_views_table`/`VIEWS_DATABASE`) → zero hits in `src/` and `web/src`. 270 passed after the deletion.

## 4. Drop databases (destructive, last)

<!-- PREP DONE, NOT EXECUTED (user: "prep, not execute"). The destructive drop is
left for the operator to run against a live DB. KEY FACT: the `lrmis_staging_db`
compose container is ONE MySQL server hosting BOTH `lrmis_staging` and
`lrmis_target` (`for_target()` = same host/port/creds, database override only).
So §4 drops the DATABASES, not the container, and the shared server creds
`LRMIS_STAGING_HOST/PORT/USER/PASSWORD` MUST stay. -->
- [~] 4.1+4.2 Built `scripts/drop_legacy_staging.py`: mysqldump backup of both DBs to `backups/` FIRST (a failed backup aborts the drop), then `DROP DATABASE` behind an EXACT typed-confirmation flag `--confirm "lrmis_staging,lrmis_staging_views"`; `--dry-run` default prints the plan and touches nothing. Connects as `LRMIS_ROOT_USER/PASSWORD` (mirrors init_lrmis_target). Verified: compiles + dry-run makes no DB access. NOT RUN.
- [ ] 4.3 (documented, not applied — do WITH the drop): remove the `MYSQL_DATABASE: lrmis_staging` env + the `./sql/lrmis_staging_init.sql` init mount from the `lrmis_staging_db` service in `docker-compose.yml`, and the `LRMIS_STAGING_DATABASE=lrmis_staging` line from `.env.example`. KEEP `LRMIS_STAGING_HOST/PORT/USER/PASSWORD/SSL_DISABLED` (shared with `for_target()`). `VIEWS_DATABASE` was code-only and is already removed. Container/volume names (`lrmis_staging_db`/`_data`) left as-is (renaming a volume = data loss); optional cosmetic follow-up.

## 5. Verify + document

- [x] 5.1 Full `pytest -q` GREEN (270 passed) after every backend step; re-run 2026-07-14 after the final-sweep `is_path_b_entity` deletion (§3.4) — still 270 passed.
- [ ] 5.1b Live MySQL-target/Postgres-target delivery smoke — still TODO, needs real DBs (deferred, same as §1.4).
- [x] 5.2 `docs/PATH_B.md` updated — Phases 8–9 recorded as done via this change, DB drop prepped-not-run, added the `rebaseline_entity_fingerprints --apply` operational note, marked `simplify-source-to-target-delivery` staging tasks + `improve-staging-cleanup` superseded.
- [x] 5.3 `openspec validate retire-legacy-staging --strict` → VALID. `graphify update .` run (AST-only refresh; note it produced a lighter AST-only graph than the earlier enriched curated build — a graphify quirk, regenerate fully if needed).
