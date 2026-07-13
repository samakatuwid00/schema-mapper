## MODIFIED Requirements

### Requirement: On-demand schema scanning

The system SHALL expose schema scanning as an API-triggered job that fingerprints the
source (PostgreSQL `irimsv`) and the target (`lrmis_target`, MySQL) schemas using the
existing ingest/fingerprint primitives, records drift when fingerprints change, and
returns a structured diff (added/removed/changed tables, columns, types, keys)
renderable by the UI. Scanning SHALL NOT reference or fingerprint any staging schema —
observability is source ↔ target only.

#### Scenario: Scan detects target drift

- **WHEN** a column type changes in the `lrmis_target` schema and an admin runs a scan
- **THEN** the scan result lists the change as breaking or non-breaking per the drift
  policy, records a drift report, and pauses impacted entities exactly as the existing
  monitor does

#### Scenario: Scan with no changes

- **WHEN** an admin runs a scan and no fingerprint changed
- **THEN** the result states no drift, and no entity state is modified

#### Scenario: No staging side scanned

- **WHEN** a schema scan runs
- **THEN** it fingerprints only the source and the `lrmis_target` schemas, and no
  `lrmis_staging` fingerprint or drift report is produced
