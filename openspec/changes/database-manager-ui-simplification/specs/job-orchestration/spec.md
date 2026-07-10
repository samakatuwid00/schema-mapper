# job-orchestration Specification (Delta)

## MODIFIED Requirements

### Requirement: Durable allowlisted jobs

The system SHALL execute admin workflows as durable jobs recorded in
`integration.admin_job`, restricted to a fixed allowlist of job types (`schema_scan`,
`discover`, `propose`, `deploy`, `backfill`, `reconcile`, `monitor`, `refresh`,
`worker_run`, `replay`, `entity_toggle`, `migration_apply`, `onboard_bulk`). Job
submission with any other type SHALL be rejected. Each job SHALL record requesting actor,
parameters, reason (when required), state (`queued`, `running`, `succeeded`, `failed`,
`cancelled`), progress, heartbeat timestamp, and structured result.

#### Scenario: Job survives an API restart

- **WHEN** the API process restarts while a job is `running`
- **THEN** the job row remains visible, and the reaper marks it `failed` with a
  stale-heartbeat error once its heartbeat exceeds the staleness threshold

#### Scenario: Unknown job type rejected

- **WHEN** a client submits a job with type `shell_exec`
- **THEN** the API responds with a validation error and no job row is created

#### Scenario: Bulk onboard is an allowlisted type

- **WHEN** a client submits a job of type `onboard_bulk` with a list of tables
- **THEN** the job is accepted and executed by the durable job runner like any other
  allowlisted type

### Requirement: Concurrent execution guards

The system SHALL prevent conflicting concurrent executions: a deploy of a proposal SHALL
take a Postgres advisory lock and re-check proposal status inside the locked transaction;
a second concurrent deploy, refresh, migration apply, or bulk onboard targeting the same
scope SHALL receive an HTTP 409 rather than executing or silently queuing behind the
first.

#### Scenario: Double deploy from two tabs

- **WHEN** the same proposal is deployed simultaneously from two browser tabs
- **THEN** exactly one deploy executes and the other receives a conflict response
  identifying the running job

#### Scenario: Overlapping bulk onboards

- **WHEN** two operators submit `onboard_bulk` for the same schema and table set at the
  same moment
- **THEN** exactly one job is created and the other receives a conflict response
