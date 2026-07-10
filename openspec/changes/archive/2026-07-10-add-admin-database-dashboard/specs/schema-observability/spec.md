# schema-observability Specification (Delta)

## ADDED Requirements

### Requirement: On-demand schema scanning

The system SHALL expose schema scanning as an API-triggered job that fingerprints the
source (PostgreSQL) and target (MySQL) schemas using the existing ingest/fingerprint
primitives, records drift when fingerprints change, and returns a structured diff
(added/removed/changed tables, columns, types, keys) renderable by the UI.

#### Scenario: Scan detects target drift

- **WHEN** a column type changes in the MySQL staging schema and an admin runs a scan
- **THEN** the scan result lists the change as breaking or non-breaking per the drift
  policy, records a drift report, and pauses impacted entities exactly as the existing
  CLI monitor does

#### Scenario: Scan with no changes

- **WHEN** an admin runs a scan and no fingerprint changed
- **THEN** the result states no drift, and no entity state is modified

### Requirement: Drift visibility and side-effect transparency

The UI SHALL list all drift reports with their fingerprints, affected entities, and
pause side effects. Because scans can pause entities as a side effect, the scan result
SHALL explicitly enumerate any entities it paused.

#### Scenario: Paused entities called out

- **WHEN** a scan pauses two entities due to breaking drift
- **THEN** the scan completion event and Drift Reports page both name the two paused
  entities and the drift report that caused it

### Requirement: Queue and entity health API

The system SHALL expose read-only endpoints for outbox counts by status and entity,
oldest pending age, quarantine entries (with errors and payload snapshots), dead-letter
events, entity control states, and onboarding entity statuses — sufficient for every
dashboard read without direct database access from the browser.

#### Scenario: Quarantine inspection

- **WHEN** an admin opens a quarantined event
- **THEN** the UI shows its validation errors, payload snapshot, attempts, and offers
  the replay action
