## MODIFIED Requirements

### Requirement: Durable allowlisted jobs

The system SHALL execute admin workflows as durable jobs recorded in
`integration.admin_job`, restricted to a fixed allowlist of job types (`schema_scan`,
`discover`, `propose`, `deploy`, `backfill`, `reconcile`, `monitor`, `refresh`,
`nightly_refresh`, `worker_run`, `replay`, `entity_toggle`, `migration_apply`). The
allowlist SHALL NOT include any staging-cleanup job type (`sweep_staging`,
`retire_entity`), which no longer exists. Job submission with any other type SHALL be
rejected. Each job SHALL record requesting actor, parameters, reason (when required),
state (`queued`, `running`, `succeeded`, `failed`, `cancelled`), progress, heartbeat
timestamp, and structured result.

#### Scenario: Job survives an API restart

- **WHEN** the API process restarts while a job is `running`
- **THEN** the job row remains visible, and the reaper marks it `failed` with a
  stale-heartbeat error once its heartbeat exceeds the staleness threshold

#### Scenario: Unknown job type rejected

- **WHEN** a client submits a job with type `shell_exec`
- **THEN** the API responds with a validation error and no job row is created

#### Scenario: Retired staging job type rejected

- **WHEN** a client submits a job with type `sweep_staging` or `retire_entity`
- **THEN** the API responds with a validation error and no job row is created

### Requirement: Concurrent execution guards

The system SHALL prevent conflicting concurrent executions: a deploy of a proposal
SHALL take a Postgres advisory lock and re-check proposal status inside the locked
transaction; a second concurrent deploy, refresh, nightly rebuild, or migration apply
targeting the same scope SHALL receive an HTTP 409 rather than executing or silently
queuing behind the first.

#### Scenario: Double deploy from two tabs

- **WHEN** the same proposal is deployed simultaneously from two browser tabs
- **THEN** exactly one deploy executes and the other receives a conflict response
  identifying the running job

#### Scenario: Overlapping nightly rebuilds serialized

- **WHEN** a second nightly rebuild is started while one is already running
- **THEN** the second receives a conflict response and does not begin restoring the
  source or resetting the target
