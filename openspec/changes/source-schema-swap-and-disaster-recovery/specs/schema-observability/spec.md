## MODIFIED Requirements

### Requirement: On-demand schema scanning

The system SHALL expose schema scanning as an API-triggered job that
fingerprints the source and target schemas (using whichever adapter pair is
configured), records drift when fingerprints change, and returns a
structured diff (added/removed/changed tables, columns, types, keys)
renderable by the UI.

#### Scenario: Scan detects target drift

- **WHEN** a column type changes in the configured target schema and an
  admin runs a scan
- **THEN** the scan result lists the change as breaking or non-breaking per
  the drift policy, records a drift report, and pauses impacted entities
  using the configured target adapter's schema discovery

#### Scenario: Scan with no changes

- **WHEN** an admin runs a scan and no fingerprint changed on either side
- **THEN** the result states no drift, and no entity state is modified

#### Scenario: Target-schema change links to schema-swap

- **WHEN** a scan detects that the target schema (or target engine) has
  changed from the approved fingerprint
- **THEN** the drift report links to the `schema-swap --side target` action,
  which re-discovers the new target, re-maps affected entities (gated), and
  re-delivers the kept entities — rather than stopping at a "drifted" status

#### Scenario: Source-schema change links to schema-swap

- **WHEN** a scan detects that the IRIMSV source schema has changed from the
  approved source contract
- **THEN** the drift report links to the `schema-swap --side source` action,
  which re-discovers the new source, re-maps only the affected entities
  (gated), and resumes delivery — rather than stopping at a "drifted" status
