## MODIFIED Requirements

### Requirement: Centralized admin web UI

The system SHALL provide a web UI at a configurable host/port that
centralizes all integration admin workflows: overview, schema scanning,
onboarding, mapping review, migrations, worker and queue management, drift
reports, recovery, and audit log. The UI SHALL NOT provide any facility to
execute arbitrary shell commands or SQL.

#### Scenario: Admin reaches all workflows from one place

- **WHEN** an authenticated admin opens the dashboard
- **THEN** navigation exposes Overview, Schema Scanner, Onboarding Wizard,
  Mapping Review, Migrations, Worker & Queues, Drift Reports, Recovery, and
  Audit Log pages without any terminal usage

#### Scenario: Recovery page lists backups and restores behind confirmation

- **WHEN** an admin opens the Recovery page in the Maintain nav group
- **THEN** they see existing automatic target backups and any uploaded
  source/target files, with a restore action gated behind typed
  confirmation for each
