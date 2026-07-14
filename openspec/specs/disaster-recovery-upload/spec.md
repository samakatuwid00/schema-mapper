# disaster-recovery-upload Specification

## Purpose
Validated file upload for source-restore recovery and target-backup recovery
— covering the two failure points this pipeline has actually hit (an
unreadable source dump, a target rebuild failing partway). Every upload is
quarantined and validated before it is ever offered as a restore candidate;
every restore is typed-confirmation gated and fully audited, reusing the
existing backup/restore primitives.
## Requirements
### Requirement: Validated file upload for recovery

The system SHALL accept an uploaded source dump or target backup file,
validate it (magic-byte format check, encoding check, and for source dumps a
check that it contains the `irimsv` schema) before offering it as a restore
candidate, and SHALL write it only to a quarantined temp path.

#### Scenario: Valid source dump is accepted

- **WHEN** an admin uploads a UTF-8 `pg_dump` custom-format file containing
  the `irimsv` schema
- **THEN** validation passes and the file appears as a selectable
  source-restore candidate

#### Scenario: UTF-16 dump is rejected with a clear reason

- **WHEN** an admin uploads a dump file encoded in UTF-16 (the historical
  failure mode recorded in `docs/RUNBOOK_source_to_target.md`)
- **THEN** validation fails and the admin sees the specific reason ("file is
  UTF-16, expected UTF-8") rather than a generic error

#### Scenario: Non-dump file is rejected

- **WHEN** an admin uploads a file that fails the magic-byte format check
- **THEN** validation fails before the file is ever offered as a restore
  candidate

#### Scenario: Uploaded file is quarantined, not directly executable

- **WHEN** a file is uploaded, valid or not
- **THEN** it is written to a quarantined temp path that no restore shell
  command executes directly, and an `integration.recovery_upload` audit row
  is recorded

### Requirement: Target backups are visible and restorable from the UI

The system SHALL list the timestamped backups `nightly_refresh` already
takes before every destructive reset, alongside any manually uploaded target
backup, and SHALL let an admin restore the target from a selected one.

#### Scenario: Existing automatic backups are listed

- **WHEN** an admin opens the Recovery page
- **THEN** every timestamped backup `nightly_refresh` has already taken is
  listed, even if none were ever uploaded manually

#### Scenario: Restoring a backup requires typed confirmation

- **WHEN** an admin selects a backup and requests a target restore
- **THEN** the system requires the same typed-confirmation pattern used by
  other destructive actions in this admin UI before executing the restore

#### Scenario: Restore is audited

- **WHEN** a target or source restore executes
- **THEN** the `recovery_upload` (or matched backup) record's
  `used_at`/`used_by` is set, and the action is recorded in the existing
  audit system

### Requirement: Recovery actions are always confirmation-gated

A recovery restore action SHALL always require explicit confirmation,
regardless of any autonomy tier the conversational agent may later support.

#### Scenario: Recovery is never auto-executed

- **WHEN** a recovery restore is initiated through any interface (UI, CLI,
  or future agent tool)
- **THEN** it always requires explicit confirmation and is never executed
  automatically
