## 1. Migration — Add `resolved_at` to `schema_drift_report`

- [x] 1.1 Create `sql/009_drift_resolution.sql` with `ALTER TABLE integration.schema_drift_report ADD COLUMN resolved_at TIMESTAMPTZ`
- [x] 1.2 Register the migration in `src/services/migrations.py` MIGRATION_FILES list

## 2. Core Service — `src/services/drift_resolution.py`

- [x] 2.1 Create module with `resolve_source_drift()` — re-scan source, compute fingerprint, refresh staging, update entity, re-enable
- [x] 2.2 Implement `resolve_target_drift()` — re-scan target staging, compute fingerprint, refresh staging, update entity, re-enable
- [x] 2.3 Implement `resolve_all()` coordinator — calls source then target resolution
- [x] 2.4 Implement dry-run mode — read-only fingerprint comparison without mutations
- [x] 2.5 Implement audit recording for each resolved entity
- [x] 2.6 Mark drift reports as resolved when all impacted entities are cleared

## 3. Job Handler — Register `resolve_drift` in Admin API

- [x] 3.1 Add `_h_resolve_drift` handler in `src/admin_api/jobs.py`
- [x] 3.2 Register in `JOB_HANDLERS` dict
- [x] 3.3 Add to `_SCOPED` concurrent-execution guards (scope: `"resolve-drift"`)
- [x] 3.4 Wire up `JobContext.progress()` for progress reporting during batch resolution

## 4. API — Endpoint Configuration

- [x] 4.1 Add `"resolve_drift"` to `_ONE_CLICK_JOBS` in `src/admin_api/routers.py` (or typed-confirmation tier — requires reason)
- [x] 4.2 Ensure `resolve_drift` followed by `resolve_source`/`resolve_target` params passes through correctly

## 5. CLI — Auto-resolve flag on Schema Monitor

- [x] 5.1 Add `--auto-resolve` flag to `scripts/schema_monitor.py`
- [x] 5.2 When set and non-breaking drift detected, call `resolve_drift()` for impacted entities after recording report

## 6. Tests

- [x] 6.1 Create `tests/test_drift_resolution.py` with unit test for `resolve_source_drift`
- [x] 6.2 Unit test for `resolve_target_drift`
- [x] 6.3 Unit test for dry-run mode (no mutations)
- [x] 6.4 Unit test for `resolve_all` coordinator
- [x] 6.5 Integration test: mock drift report, run resolution, verify entity state changes
- [x] 6.6 Test job handler accepts valid params and rejects invalid ones
- [x] 6.7 Run full test suite: `pytest -q`
