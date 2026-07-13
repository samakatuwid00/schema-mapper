# schema-observability Specification (Delta)

## ADDED Requirements

### Requirement: Second drift pair — staging (Path A) to target (Path B)

The system SHALL additionally observe the schema contract of the canonical target
database (`lrmis_target`, Path B) and detect drift between it and the staging
contract (`lrmis_staging`, Path A). This drift pair SHALL be tracked
independently from the existing source→staging drift.

#### Scenario: Staging→target drift detected

- **WHEN** a column changes in the `lrmis_target` canonical table and an admin
  runs a scan in `staging` mode
- **THEN** the scan records a drift report with `drift_pair='staging->target'`,
  lists the change as breaking or non-breaking, and names the impacted staging
  entities

#### Scenario: Staging→target scan with no change

- **WHEN** an admin runs a `staging`-mode scan and the Path B contract matches
  the staging contract
- **THEN** the result states no staging→target drift and no entity state is
  modified

### Requirement: Three schema trees

The `/api/schemas` endpoint SHALL return three trees: `source` (IRIMSV),
`staging` (`lrmis_staging` contract), and `target_b` (`lrmis_target` canonical
contract), each with its fingerprint and column layout.

#### Scenario: Target (Path B) tree available

- **WHEN** an authenticated operator requests `/api/schemas`
- **THEN** the response includes a `target_b` tree fingerprinted from
  `lrmis_target`

## MODIFIED Requirements

### Requirement: On-demand schema scanning

The system SHALL expose schema scanning as an API-triggered job that fingerprints
the source (PostgreSQL) and target (MySQL) schemas using the existing
ingest/fingerprint primitives, records drift when fingerprints change, and returns a
structured diff renderable by the UI. The scan SHALL accept a `mode` selecting
which pair is observed: `source` (IRIMSV vs staging, default) or `staging`
(staging vs Path B target).

#### Scenario: Scan with no changes

- **WHEN** an admin runs a scan in either mode and no fingerprint changed
- **THEN** the result states no drift for that pair, and no entity state is
  modified
