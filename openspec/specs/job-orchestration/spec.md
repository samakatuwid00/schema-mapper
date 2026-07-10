# job-orchestration Specification

## Purpose
TBD - created by archiving change add-admin-database-dashboard. Update Purpose after archive.
## Requirements
### Requirement: Durable allowlisted jobs

The system SHALL execute admin workflows as durable jobs recorded in
`integration.admin_job`, restricted to a fixed allowlist of job types (`schema_scan`,
`discover`, `propose`, `deploy`, `backfill`, `reconcile`, `monitor`, `refresh`,
`worker_run`, `replay`, `entity_toggle`, `migration_apply`). Job submission with any
other type SHALL be rejected. Each job SHALL record requesting actor, parameters,
reason (when required), state (`queued`, `running`, `succeeded`, `failed`,
`cancelled`), progress, heartbeat timestamp, and structured result.

#### Scenario: Job survives an API restart

- **WHEN** the API process restarts while a job is `running`
- **THEN** the job row remains visible, and the reaper marks it `failed` with a
  stale-heartbeat error once its heartbeat exceeds the staleness threshold

#### Scenario: Unknown job type rejected

- **WHEN** a client submits a job with type `shell_exec`
- **THEN** the API responds with a validation error and no job row is created

### Requirement: Live job event streaming

The system SHALL emit an `integration.admin_job_event` row for each job state change
and progress tick, and stream these to clients over Server-Sent Events so that any
open dashboard observes job progress without polling.

#### Scenario: Two admins watch the same job

- **WHEN** admin A starts a backfill and admin B has the Worker & Queues page open
- **THEN** admin B sees the job appear and its progress advance via the event stream

### Requirement: Concurrent execution guards

The system SHALL prevent conflicting concurrent executions: a deploy of a proposal
SHALL take a Postgres advisory lock and re-check proposal status inside the locked
transaction; a second concurrent deploy, refresh, or migration apply targeting the same
scope SHALL receive an HTTP 409 rather than executing or silently queuing behind the
first.

#### Scenario: Double deploy from two tabs

- **WHEN** the same proposal is deployed simultaneously from two browser tabs
- **THEN** exactly one deploy executes and the other receives a conflict response
  identifying the running job

### Requirement: Controllable delivery worker

The delivery worker SHALL be startable and stoppable from the UI as a standing loop
with a configurable interval, and SHALL also support a single one-shot pass. The UI
SHALL always display whether the loop is running and since when.

#### Scenario: Worker loop stop takes effect

- **WHEN** an operator stops a running worker loop
- **THEN** the loop exits after at most one in-flight batch and its status shows
  stopped with an audit record of who stopped it

