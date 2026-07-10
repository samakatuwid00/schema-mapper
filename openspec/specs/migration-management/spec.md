# migration-management Specification

## Purpose
TBD - created by archiving change add-admin-database-dashboard. Update Purpose after archive.
## Requirements
### Requirement: Tracked migration state

The system SHALL track applied SQL migrations in
`integration.schema_migrations(filename, checksum, applied_at, applied_by, success)`
and SHALL display, for the ordered central-database migration file list, which files
are applied (with when and by whom) and which are pending.

#### Scenario: Migrations page reflects reality

- **WHEN** an admin opens the Migrations page on a database where two of three files
  were applied via the tracker
- **THEN** the two applied files show their applied_at/applied_by and the third shows
  as pending with an Apply action

### Requirement: Safe migration apply

Applying a migration SHALL: acquire a fixed Postgres advisory lock, verify the file's
sha256 checksum against any previously recorded checksum for that filename (hard-fail
on mismatch before executing any SQL), execute the whole file inside one transaction,
and record the tracking row only after commit. Apply SHALL require `admin` role and
typed confirmation of the filename plus a reason, with a preview of the SQL and the
target DSN.

#### Scenario: Edited already-applied file is rejected

- **WHEN** an admin attempts to re-apply a migration file whose content no longer
  matches its recorded checksum
- **THEN** the apply is rejected before any SQL executes and the mismatch is shown in
  the UI

#### Scenario: Failed migration is a no-op

- **WHEN** a migration file fails partway through execution
- **THEN** the transaction rolls back, no tracking row is recorded, and the failure
  message is surfaced

#### Scenario: Concurrent applies serialized

- **WHEN** two admins click Apply on migrations at the same moment
- **THEN** one proceeds and the other receives a conflict response; they never
  interleave

### Requirement: Idempotent foundation SQL

`sql/001_integration_foundation.sql` SHALL be idempotent (`IF NOT EXISTS` on its
`CREATE` statements and guarded enum creation) so that databases initialized via the
Docker init-mount and databases managed via the tracker can both re-run it without
error.

#### Scenario: Re-apply over Docker-initialized volume

- **WHEN** the foundation file is applied via the tracker against a database that
  already ran it through the Docker init-mount
- **THEN** it completes without "already exists" errors and the tracker records it

