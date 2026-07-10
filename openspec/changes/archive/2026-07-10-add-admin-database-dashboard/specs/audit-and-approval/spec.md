# audit-and-approval Specification (Delta)

## ADDED Requirements

### Requirement: Per-admin authentication with roles

The system SHALL authenticate admins against `integration.admin_user` (bcrypt password
hashes, active flag) using signed session cookies, with two roles: `operator` (may run
workflows, toggles, replays, approvals) and `admin` (additionally migrations and user
management). Unauthenticated requests to any non-login endpoint SHALL be rejected.
Client-supplied actor names SHALL NOT be accepted anywhere — the acting identity always
comes from the session.

#### Scenario: Operator blocked from migrations

- **WHEN** an `operator` attempts to apply a migration
- **THEN** the API responds with a forbidden error and no job is created

#### Scenario: Spoofed actor field ignored

- **WHEN** a request body includes an `actor` or `by` field differing from the session
  user
- **THEN** the audit row and all attribution use the session user

### Requirement: Uniform audit trail on every mutation

Every mutating endpoint SHALL write an `integration.admin_action_audit` row (actor,
action, target_type, target_id, request_id, reason, details, result, error_message,
performed_at) in the same database transaction as the mutation itself. Failed attempts
SHALL also be audited with their error.

#### Scenario: Kill switch toggle audited

- **WHEN** an operator disables entity `customer` with reason "contract review"
- **THEN** exactly one audit row records that operator, the entity, the reason, and
  success — atomically with the `entity_control` update

#### Scenario: Audit log is browsable

- **WHEN** an admin opens the Audit Log page
- **THEN** actions are listed newest-first with actor, action, target, reason, and
  result, filterable by actor, action type, and date range

### Requirement: Approval remains a human gate

AI-generated mapping proposals SHALL only activate through an authenticated admin
approval/deploy action in the UI (or the existing CLI). Nothing in the admin API SHALL
auto-approve proposals or auto-apply schema changes.

#### Scenario: Proposal cannot self-activate

- **WHEN** a propose job completes with high-confidence mappings
- **THEN** the proposal remains in a non-deployed status until a human triggers deploy
  with confirmation

### Requirement: Reversibility affordances for dangerous actions

Kill-switch disable SHALL offer an immediate re-enable affordance. Refresh and redeploy
SHALL snapshot the staging table before dropping it (keeping at least the most recent
snapshot) and SHALL offer restore-from-snapshot. Replay SHALL be presented as guarded
(idempotent delivery) rather than undoable.

#### Scenario: Restore after refresh

- **WHEN** an operator refreshes entity `customer` and the reload produces wrong data
- **THEN** the operator can restore the pre-refresh snapshot of the staging table from
  the UI, and the restore is audited
