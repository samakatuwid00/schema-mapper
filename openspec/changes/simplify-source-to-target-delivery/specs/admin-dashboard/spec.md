## MODIFIED Requirements

### Requirement: One-click workflow launch with guarded tiers

The system SHALL start safe workflows (schema scan, discover, status refresh, single
replay, single kill-switch toggle, single worker pass) with one click. The system SHALL
require a confirmation modal with a required reason for deploy, backfill, worker-loop
start/stop, and schema approval. The system SHALL require the admin to type the target
name plus a reason for refresh, redeploy of a deployed entity, migration apply, the
**nightly rebuild** (which restores the source and resets the target), and bulk
kill-switch disable.

#### Scenario: Safe action runs immediately

- **WHEN** an operator clicks "Scan schema now"
- **THEN** a scan job starts without any confirmation dialog

#### Scenario: Destructive action demands typed confirmation

- **WHEN** an operator clicks "Refresh" on entity `customer`
- **THEN** the action only executes after the operator types `customer` and provides a
  reason, and the dialog first displays the current target row count

#### Scenario: Nightly rebuild demands typed confirmation

- **WHEN** an operator starts the nightly rebuild
- **THEN** the action only executes after the operator types the configured target name
  and provides a reason, and the dialog states that the source will be restored and the
  target reset

## ADDED Requirements

### Requirement: Nightly rebuild control and status

The dashboard SHALL present the nightly rebuild as a single, plainly-labeled control
(not split across "Migration" and "Backfill" language) with a dedicated status panel.
The panel SHALL show whether a rebuild is currently in progress, and for the last run:
when it ran, who ran it, and per-entity delivered and quarantined counts. Data movement
SHALL be presented uniformly as "delivery"; the UI SHALL NOT present a staging browser
or staging-specific pages.

#### Scenario: Rebuild in progress is visible

- **WHEN** a nightly rebuild is running
- **THEN** the status panel shows "rebuild in progress" so the target is not misread as
  a finished copy while it is still being filled

#### Scenario: Last run is summarized

- **WHEN** a nightly rebuild has completed
- **THEN** the panel shows its timestamp, actor, and per-entity delivered/quarantined
  counts

#### Scenario: No staging surface

- **WHEN** an operator navigates the dashboard
- **THEN** there is no staging data browser or staging-specific page, and data movement
  is labeled "delivery" throughout
