## ADDED Requirements

### Requirement: Retire a staging entity

The system SHALL provide a `retire_entity` operation that snapshots an entity's staging table aside, drops it, and marks the entity `disabled` so it stops delivering. It SHALL support a dry-run that mutates nothing.

#### Scenario: Retire drops and disables

- **WHEN** an operator retires a deployed entity
- **THEN** the system snapshots its `irimsv_*_staging` table aside, drops it, sets the entity `status = 'disabled'` and `staging_table = NULL`, and returns the snapshot name

#### Scenario: Dry-run retire mutates nothing

- **WHEN** `retire_entity` is invoked with `dry_run = True`
- **THEN** no table is dropped, the entity status is unchanged, and the result reports `dropped = false`

#### Scenario: Unknown entity is rejected

- **WHEN** `retire_entity` is called with an id that does not exist
- **THEN** a not-found error is raised and nothing is mutated

### Requirement: Sweep orphan staging tables

The system SHALL provide a `sweep_orphans` operation that drops every `irimsv_*_staging` table not owned by a currently deployed entity, snapshotting each aside before the drop. It SHALL enumerate candidate tables from `information_schema.tables` (one row per table) rather than per-column metadata.

#### Scenario: Only orphans are dropped

- **WHEN** a sweep runs against a database with a mix of live and orphaned staging tables
- **THEN** tables owned by deployed entities are left untouched and each orphan is snapshotted then dropped

#### Scenario: View-generated tables are ignored

- **WHEN** a sweep encounters a `_for_lrmis` view-generated table
- **THEN** it is not treated as an orphan and is never dropped

#### Scenario: Dry-run sweep mutates nothing

- **WHEN** a sweep runs with `dry_run = True`
- **THEN** orphans are reported but no snapshot or drop is performed

### Requirement: Connector lifecycle symmetry

The staging connector SHALL expose a `close()` method for interface symmetry with the central connector, and cleanup operations SHALL close any connector they created.

#### Scenario: Owned connectors are closed

- **WHEN** `sweep_orphans` or `retire_entity` creates its own central and staging connectors
- **THEN** both are closed in the `finally` block when the operation returns

### Requirement: Cleanup result visibility

Sweep and retire jobs SHALL return a structured result — orphans found, dropped, and snapshots for a sweep; entity, source/staging tables, dropped flag, and snapshot for a retire — that the admin UI can render.

#### Scenario: Sweep result carries the lists

- **WHEN** a sweep job completes
- **THEN** its result contains `orphans_found`, `dropped`, and `snapshots` lists plus the `dry_run` flag
