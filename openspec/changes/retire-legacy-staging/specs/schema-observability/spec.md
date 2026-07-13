## MODIFIED Requirements

### Requirement: On-demand schema scanning

Schema scanning SHALL fingerprint and diff the source and the real target only;
the `lrmis_staging` / `lrmis_staging_views` databases SHALL NOT appear in any
scan, drift report, or fingerprint.

#### Scenario: Scan covers source and real target only

- **WHEN** an admin runs a schema scan
- **THEN** the result covers the source and the real target schema, and lists no
  `irimsv_*_staging` table or staging database

#### Scenario: Drift never references staging

- **WHEN** a drift report is produced
- **THEN** it references only source and real-target objects
