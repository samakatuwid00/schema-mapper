# admin-dashboard Specification

## Purpose
TBD - created by archiving change add-admin-database-dashboard. Update Purpose after archive.
## Requirements
### Requirement: Centralized admin web UI

The system SHALL provide a web UI at a configurable host/port that centralizes all
integration admin workflows: overview, schema scanning, onboarding, mapping review,
migrations, worker and queue management, drift reports, and audit log. The UI SHALL NOT
provide any facility to execute arbitrary shell commands or SQL.

#### Scenario: Admin reaches all workflows from one place

- **WHEN** an authenticated admin opens the dashboard
- **THEN** navigation exposes Overview, Schema Scanner, Onboarding Wizard, Mapping
  Review, Migrations, Worker & Queues, Drift Reports, and Audit Log pages without any
  terminal usage

### Requirement: Live overview of integration health

The Overview page SHALL show, refreshed automatically at an interval of 5 seconds or
less: outbox counts grouped by status, oldest pending event age, per-entity onboarding
status, entity kill-switch states, unresolved drift reports, and unresolved quarantine
count.

#### Scenario: Queue depth updates without reload

- **WHEN** a new outbox event is inserted while the Overview page is open
- **THEN** the pending count reflects it within 5 seconds without a manual page reload

#### Scenario: Drift alert surfaces on overview

- **WHEN** a schema drift report exists with no resolution
- **THEN** the Overview page shows a visible drift alert linking to the Drift Reports
  page

### Requirement: One-click workflow launch with guarded tiers

The system SHALL start safe workflows (schema scan, discover, status refresh, single
replay, single kill-switch toggle, single worker pass) with one click. The system SHALL
require a confirmation modal with a required reason for deploy, backfill, worker-loop
start/stop, and schema approval. The system SHALL require the admin to type the target
name plus a reason for refresh, redeploy of a deployed entity, migration apply, and
bulk kill-switch disable.

#### Scenario: Safe action runs immediately

- **WHEN** an operator clicks "Scan schema now"
- **THEN** a scan job starts without any confirmation dialog

#### Scenario: Destructive action demands typed confirmation

- **WHEN** an operator clicks "Refresh" on entity `customer`
- **THEN** the action only executes after the operator types `customer` and provides a
  reason, and the dialog first displays the current staging row count

### Requirement: Database-focused presentation

The UI SHALL present schemas as expandable trees (schema → table → column with types
and keys), mappings as source-to-target lanes with per-field confidence and status,
queues as health cards, and statuses as colored chips using the existing outbox and
entity status enums verbatim.

#### Scenario: Mapping review shows lanes

- **WHEN** an admin opens a mapping proposal
- **THEN** each field mapping is rendered as a source-column-to-target-column row with
  its confidence score, transform, and review status, and unmet required target columns
  are highlighted

