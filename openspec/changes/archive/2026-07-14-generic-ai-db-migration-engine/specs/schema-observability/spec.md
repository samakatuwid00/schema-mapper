## MODIFIED Requirements

### Requirement: On-demand schema scanning

The system SHALL expose schema scanning as an API-triggered job that fingerprints the
source and target schemas (using whichever adapter pair is configured), records drift
when fingerprints change, and returns a structured diff (added/removed/changed tables,
columns, types, keys) renderable by the UI.

#### Scenario: Scan detects target drift on any engine

- **WHEN** a column type changes in the MSSQL target schema and an admin runs a scan
- **THEN** the scan result lists the change as breaking or non-breaking per the drift policy, records a drift report, and pauses impacted entities using the MSSQL adapter's schema discovery

#### Scenario: Drift detection across different engines

- **WHEN** the source is PostgreSQL and the target is Snowflake
- **THEN** the scanner uses `PostgresSourceAdapter` and `SnowflakeTargetAdapter` to discover and fingerprint schemas independently, reporting drift per the generic type system

#### Scenario: Target-schema change links to schema-swap

- **WHEN** a scan detects that the target schema (or target engine) has changed from the approved fingerprint
- **THEN** the drift report links to the `schema-swap` action, which re-discovers the new target, re-maps affected entities (gated), and re-delivers the kept entities — rather than stopping at a "drifted" status
