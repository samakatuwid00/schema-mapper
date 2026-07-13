## MODIFIED Requirements

### Requirement: Tracked migration state

The system SHALL track applied SQL migrations in
`integration.schema_migrations(filename, checksum, applied_at, applied_by, success, engine_type)`
and SHALL display, for the ordered migration file list (per connected engine), which files
are applied (with when, by whom, and to which engine) and which are pending.

#### Scenario: Migrations page shows multi-engine state

- **WHEN** an admin opens the Migrations page on a system with Postgres source and MySQL target engines
- **THEN** the page displays separate migration lists per engine, each showing applied and pending files

#### Scenario: Scoped migration apply

- **WHEN** an admin applies a migration to the MySQL target
- **THEN** the migration is run against the target engine's connection and tracked with `engine_type = 'mysql'`
