## ADDED Requirements

### Requirement: One orchestrated nightly rebuild

The system SHALL provide a single operation that rebuilds the target from the current source in one run, composing three ordered steps: (1) restore the fresh source (`lrmis`) dump into the central database, (2) reset the target, and (3) re-deliver every onboarded entity's current source rows into the target. The operation SHALL be runnable both on a schedule and on demand, and SHALL be idempotent — a re-run reproduces the same target because the source is authoritative.

#### Scenario: Nightly rebuild produces a current target

- **WHEN** the nightly rebuild runs against a fresh source dump
- **THEN** the source is restored into central, the target is reset, all current source rows are delivered, and the target row counts match the source

#### Scenario: Re-run reproduces the same result

- **WHEN** the nightly rebuild is run twice against the same source dump
- **THEN** the second run yields the same target contents as the first, with no duplicate rows

### Requirement: Guarded destructive source restore

The source-restore step SHALL be treated as a destructive operation: it SHALL require typed confirmation of the target database name/DSN plus a reason, SHALL take the same advisory-lock guard used by deploy and refresh so two rebuilds cannot interleave, and SHALL write an audit record naming the actor, the dump, and the outcome. If the restore step fails, the operation SHALL abort before any target table is modified.

#### Scenario: Restore requires typed confirmation

- **WHEN** an operator starts the nightly rebuild from the UI
- **THEN** the rebuild only proceeds after the operator types the configured target name and provides a reason, and an audit record is written

#### Scenario: Failed restore leaves the target untouched

- **WHEN** the source-restore step fails partway
- **THEN** the operation stops, the target is not reset or re-delivered, and the failure is surfaced

### Requirement: Target reset by truncate with FK-closure reseed

The target reset SHALL TRUNCATE every pipeline-written target table and reseed only the fixed FK-closure lookup set (the reference tables the source does not carry, e.g. `psgc`), then leave the target ready for delivery. Foreign-key enforcement MAY be disabled only for the truncate-and-reseed window and SHALL be re-enabled before delivery begins, so that re-delivery still enforces referential integrity. LRMIS-owned data tables are not preserved across a reset by design — the target is a fully-rebuilt deliverable copy.

#### Scenario: Lookups survive, data tables are rebuilt

- **WHEN** the target reset runs
- **THEN** the FK-closure lookup tables (e.g. `psgc`) contain their seed rows and every pipeline-written data table is empty, ready for fresh delivery

#### Scenario: FK integrity enforced during delivery

- **WHEN** delivery begins after a reset
- **THEN** foreign-key checks are enabled and a row referencing a missing lookup would be rejected, not silently inserted

### Requirement: Pre-reset safety and dry-run preview

Before performing the destructive reset, the operation SHALL capture a recoverable backup of the target (e.g. a timestamped dump) and SHALL offer a dry-run that reports the current source and target row counts without mutating anything. The nightly control SHALL show, for the last run, when it ran, who ran it, and its per-entity delivered/quarantined counts.

#### Scenario: Dry run changes nothing

- **WHEN** an operator runs the nightly rebuild in dry-run mode
- **THEN** it reports the source and target row counts and makes no change to either database

#### Scenario: Backup taken before reset

- **WHEN** a real (non-dry-run) rebuild begins
- **THEN** a timestamped target backup is produced before the first TRUNCATE, and its location is recorded in the run result
