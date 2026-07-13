## MODIFIED Requirements

### Requirement: Durable allowlisted jobs

The system SHALL execute admin workflows as durable jobs recorded in `integration.admin_job`, restricted to a fixed allowlist of job types (`schema_scan`, `discover`, `propose`, `deploy`, `backfill`, `reconcile`, `monitor`, `refresh`, `refresh_all`, `worker_run`, `replay`, `entity_toggle`, `cancel_queue`, `migration_apply`, `resolve_drift`). Job submission with any other type SHALL be rejected. Each job SHALL record requesting actor, parameters, reason (when required), state (`queued`, `running`, `succeeded`, `failed`, `cancelled`), progress, heartbeat timestamp, and structured result.

#### Scenario: Unknown job type rejected

- **WHEN** a client submits a job with type `shell_exec`
- **THEN** the API responds with a validation error and no job row is created

#### Scenario: Resolve drift job runs

- **WHEN** a client submits a job with type `resolve_drift` and valid parameters
- **THEN** the job executes the drift resolution workflow and returns a structured result with per-entity status
