## Context

The system currently detects schema drift (both source-side from IRIMSV and target-side from LRMIS) via per-entity fingerprint comparison. When drift is detected, the entity is auto-paused and a drift report is recorded. The only resolution path today is manual: an admin must `refresh()` each entity individually, then `approve_schema()` the new fingerprint. There is no batch or automated resolution workflow.

The `monitor()` function in `ops.py` already has the building blocks: fingerprint comparison, entity pause, drift report recording. The `refresh()` function has: snapshot, drop staging table, recreate, bulk insert. What's missing is the coordination layer that chains these together with automatic fingerprint update and entity re-enable.

## Goals / Non-Goals

**Goals:**
- Single action to resolve all drifted entities (source-side, target-side, or both)
- Per-entity resolution from the drift report page
- Dry-run mode: preview without side effects
- Re-use existing `refresh()` and schema discovery primitives — no reimplementation
- Transaction safety: all-or-nothing within each entity's resolution
- Audit trail: each resolution recorded in `integration.onboarding_audit`

**Non-Goals:**
- Automatic resolution of breaking schema changes without admin review (breaking changes still require manual approval)
- Schema evolution/migration beyond simple re-scan and refresh
- Cross-entity dependency ordering during bulk resolution (entities are independent)
- Rollback of resolved drift (snapshot mechanism already exists via `snapshot_staging_table`)

## Decisions

### 1. New module vs. extending `ops.py`

**Decision:** Create `src/services/drift_resolution.py`.

**Rationale:** `ops.py` is already 640 lines covering status, quarantine, proposals, monitoring, refresh, reconciliation, and entity control. Adding drift resolution would push it past 700 lines with a logically distinct concern. A dedicated module keeps the resolution workflow self-contained and testable. The module imports `_discover_source_schema` from `pipeline` and reuses `refresh()` from `ops` — no code duplication.

### 2. Resolve flows: source-side vs. target-side

**Decision:** Two separate functions (`resolve_source_drift`, `resolve_target_drift`) plus a coordinator (`resolve_all`).

**Rationale:** The operations are different enough:
- Source-side: re-scan Postgres `information_schema` via `_discover_source_schema`, update `source_fingerprint`, then refresh staging table
- Target-side: re-scan MySQL `information_schema` via `from_information_schema`, update `target_fingerprint`, then recreate staging table with new shape

Combining them into one function would require branching on direction throughout. Separating them keeps each path clean, and `resolve_all` just calls both.

### 3. Dry-run as read-only mode

**Decision:** Pass `dry_run=True` to skip all mutations, returning the plan instead.

**Rationale:** Dry-run reuses the same code paths but skips the final SQL writes. It reads current state, computes new fingerprints, compares to stored ones, and returns a report. No temporary tables, no rollback — simpler and safer than running in a transaction and aborting.

### 4. Auto-resolve integration with monitor

**Decision:** The `schema_monitor.py` CLI gain a `--auto-resolve` flag that, when set, calls `resolve_all()` for impacted entities after recording drift. Default off — admin must opt in.

**Rationale:** Breaking changes should always be reviewed by an admin. The auto-resolve flag is safe for non-breaking drifts (e.g., column added with a default value) where the admin trusts the automated resolution. Keeping it opt-in ensures no surprises in production.

### 5. Scoped concurrent execution

**Decision:** `resolve_drift` is a whole-system scope (`"resolve-drift"`) that blocks concurrent runs. No per-entity scoping.

**Rationale:** Multiple resolve runs on the same system at the same time would re-read and overwrite each other's fingerprints. A single global scope is simpler and sufficient — resolution is fast (one DB query per entity) and should finish in seconds even for dozens of entities.

## Risks / Trade-offs

- **[Risk] Staging table refresh fails partway through a batch** → Each entity's resolution is idempotent: a failed entity can be retried individually. The `refresh()` function already snapshots before dropping, so manual rollback is possible.
- **[Risk] Fingerprint updated but staging table refresh never ran** → The function updates fingerprints only after the staging table is successfully populated and committed. Transaction ordering prevents this.
- **[Trade-off] Bulk resolution is serial per entity** → Could be parallelized, but the refresh step already bulk-inserts 1000 rows at a time, so serial execution is fast enough. Parallel would add complexity with no measurable benefit for typical entity counts (<100).
- **[Risk] Entity has no approved mapping** → Pre-flight check lists which entities would be skipped and why, before any mutations.
