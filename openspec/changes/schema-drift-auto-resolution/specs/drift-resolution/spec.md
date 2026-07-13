## ADDED Requirements

### Requirement: Resolve source-side drift

The system SHALL provide a `resolve_source_drift` operation that, for one or more entities paused by source-side schema drift, re-scans the source system, refreshes the staging table, updates the entity fingerprint, and re-enables delivery.

#### Scenario: Resolve single source-drifted entity

- **WHEN** an entity is paused due to source-schema drift and an admin triggers `resolve_source_drift` for that entity
- **THEN** the system re-scans the source `information_schema` for the entity's table, computes a new `source_fingerprint`, drops and recreates the staging table, bulk-loads data from source, updates `integration.onboarding_entity.source_fingerprint`, clears `paused_reason`, sets `status = 'deployed'`, and re-enables `integration.entity_control`

#### Scenario: Resolve all source-drifted entities

- **WHEN** an admin triggers `resolve_source_drift` without specifying entities
- **THEN** the system queries all entities paused with a source-drift reason, resolves each one, and returns per-entity results

### Requirement: Resolve target-side drift

The system SHALL provide a `resolve_target_drift` operation that re-scans the target staging schema, updates entity fingerprints, drops/recreates staging tables with the new shape, repopulates from source, and re-enables delivery.

#### Scenario: Resolve single target-drifted entity

- **WHEN** an entity is paused due to target-schema drift and an admin triggers `resolve_target_drift` for that entity
- **THEN** the system re-scans the MySQL staging `information_schema` for the entity's staging table, computes a new `target_fingerprint`, drops and recreates the staging table, bulk-loads data from source, updates `integration.onboarding_entity.target_fingerprint`, clears pause, and re-enables delivery

#### Scenario: Staging table shape changed on target

- **WHEN** a column was added to the LRMIS staging table and the entity is paused
- **THEN** `resolve_target_drift` recreates the staging table with the new column, and new rows from source include data for that column per the existing mapping

### Requirement: Dry-run mode

The system SHALL support a read-only dry-run that reports what would change without mutating any state.

#### Scenario: Dry-run reports affected entities

- **WHEN** an admin runs `resolve_drift` with `dry_run=True`
- **THEN** the system returns a list of affected entities with their current fingerprint, new fingerprint, drift status, and what action would be taken — without modifying any database rows or staging tables

### Requirement: Bulk resolve all directions

The system SHALL provide a `resolve_all` operation that resolves source-side and target-side drift in a single invocation.

#### Scenario: Bulk resolve source and target drift

- **WHEN** an admin triggers `resolve_all` 
- **THEN** the system resolves all source-drifted entities first, then all target-drifted entities, and returns a combined report with per-entity resolution status

### Requirement: Audit trail for resolution

Every resolution action SHALL record an audit entry in `integration.onboarding_audit`.

#### Scenario: Resolution is audited

- **WHEN** a drift resolution completes for an entity
- **THEN** an audit row is inserted with `action = 'drift_resolved'`, `details` containing old/new fingerprints, and `performed_by` set to the requesting actor

### Requirement: Drift report resolved_at tracking

The `integration.schema_drift_report` table SHALL have a `resolved_at` column that is set when a drift is resolved.

#### Scenario: Resolved drift report timestamped

- **WHEN** all impacted entities for a drift report are resolved
- **THEN** the drift report's `resolved_at` is set to the current timestamp
