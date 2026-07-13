## MODIFIED Requirements

### Requirement: Job-type allowlist

The job runner SHALL accept only jobs on an explicit allowlist, and that
allowlist SHALL NOT include staging-only jobs (`sweep_staging`, `retire_entity`) —
staging no longer exists, so nothing enqueues or executes staging cleanup.

#### Scenario: Staging jobs are rejected

- **WHEN** a `sweep_staging` or `retire_entity` job is enqueued
- **THEN** the runner rejects it as an unknown job type

#### Scenario: Delivery enqueues no staging work

- **WHEN** the delivery worker processes a batch
- **THEN** it never enqueues a staging-cleanup job
