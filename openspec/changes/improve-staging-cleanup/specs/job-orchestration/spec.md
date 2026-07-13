## MODIFIED Requirements

### Requirement: Durable allowlisted jobs

The job type allowlist SHALL include `retire_entity` and `sweep_staging`, each registered with a concurrent-execution scope so a second run on the same target is rejected while one is in flight.

#### Scenario: Cleanup jobs are allowlisted and scoped

- **WHEN** a client submits a `sweep_staging` or `retire_entity` job with valid parameters
- **THEN** the job executes the corresponding cleanup workflow and returns its structured result

#### Scenario: Concurrent cleanup on the same target is rejected

- **WHEN** a `retire_entity` job is already queued or running for an entity id and another is submitted for the same id
- **THEN** the second submission is rejected by the scope guard
