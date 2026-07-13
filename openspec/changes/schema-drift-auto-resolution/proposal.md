## Why

When the IRIMSV source system or LRMIS target system changes schema (inevitable in national/regional systems), the system detects drift and pauses entities — but there is no automated resolution. An admin must manually refresh each paused entity one by one, then approve the new schema fingerprint. This is slow, error-prone, and doesn't scale.

## What Changes

- **New `resolve_drift` workflow** that chains: detect drift → re-scan source/target → drop+recreate staging tables → re-populate → update fingerprints → re-enable entities
- **New `resolve_drift` job type** registered in the allowlist with concurrent-execution scope guard
- **Source-side resolution**: re-scans Postgres `information_schema` for the source table, computes new fingerprint, drops + recreates + repopulates the staging table, clears pause
- **Target-side resolution**: re-scans MySQL `information_schema` for the staging table, computes new fingerprint, drops + recreates + repopulates, clears pause
- **Dry-run mode**: preview which entities would change and their current vs new fingerprints, without mutating anything
- **Optional auto-resolve flag** on `schema_monitor.py` for non-breaking drifts

## Capabilities

### New Capabilities
- `drift-resolution`: Automated resolution of schema drift — re-scan source/target, refresh staging tables, update fingerprints, re-enable entities in a single coordinated workflow

### Modified Capabilities
- `schema-observability`: The drift monitoring and reporting capability gains a resolution counterpart — drift reports now link to a resolve action
- `job-orchestration`: Extend the job type allowlist to include `resolve_drift` with concurrent-execution scope guard

## Impact

- **New module**: `src/services/drift_resolution.py` — the orchestration service
- **New migration**: `sql/009_drift_resolution.sql` — adds `resolved_at` column to `integration.schema_drift_report`
- **API**: New `_h_resolve_drift` handler in `src/admin_api/jobs.py`; `resolve_drift` added to `JOB_HANDLERS` and `_SCOPED`
- **CLI**: `--auto-resolve` flag on `scripts/schema_monitor.py`
- **Web UI**: Drift report page gets a "Resolve Drift" action per entity and a bulk "Resolve All" action
- **Tests**: New `tests/test_drift_resolution.py` — unit + integration tests
