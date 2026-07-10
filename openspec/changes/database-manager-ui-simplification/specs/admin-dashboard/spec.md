# admin-dashboard Specification (Delta)

## MODIFIED Requirements

### Requirement: Centralized admin web UI

The system SHALL provide a web UI at a configurable host/port that centralizes all
integration admin workflows, presented in three navigation groups: monitoring (overview,
data browser, sync queue), setup (tables, review queue), and maintenance (schema changes,
database updates, audit log). The UI SHALL NOT provide any facility to execute arbitrary
shell commands or SQL.

#### Scenario: Admin reaches all workflows from one place

- **WHEN** an authenticated admin opens the dashboard
- **THEN** navigation exposes, grouped by purpose, the overview, data browser, sync queue,
  tables, review queue, schema changes, database updates, and audit log pages without any
  terminal usage

#### Scenario: Schema scanning and drift live together

- **WHEN** an admin opens the schema changes page
- **THEN** both the scan trigger and the resulting drift reports are on that one page

### Requirement: Live overview of integration health

The Overview page SHALL show, refreshed automatically at an interval of 5 seconds or
less: outbox counts grouped by status, oldest pending event age, per-entity onboarding
status, entity kill-switch states, unresolved drift reports, and unresolved quarantine
count. It SHALL additionally visualize the source-to-target delivery pipeline and recent
throughput.

#### Scenario: Queue depth updates without reload

- **WHEN** a new outbox event is inserted while the Overview page is open
- **THEN** the pending count reflects it within 5 seconds without a manual page reload

#### Scenario: Drift alert surfaces on overview

- **WHEN** a schema drift report exists with no resolution
- **THEN** the Overview page shows a visible drift alert linking to the schema changes
  page

#### Scenario: Pipeline state is visible at a glance

- **WHEN** events are backing up in the queue or rows are blocked
- **THEN** the pipeline visualization reflects that state rather than showing a healthy
  flow

### Requirement: Database-focused presentation

The UI SHALL present schemas as expandable trees (schema → table → column with types
and keys), mappings as source-to-target lanes with per-field confidence and status,
queues as health cards, and statuses as colored chips using the existing outbox and
entity status enums verbatim. Each status color SHALL carry a single meaning, and every
chip SHALL include a text label so that no state is conveyed by color alone. The UI SHALL
use vector icons rather than emoji or unicode glyphs, and SHALL honor
`prefers-reduced-motion`.

#### Scenario: Mapping review shows lanes

- **WHEN** an admin opens a mapping proposal
- **THEN** each field mapping is rendered as a source-column-to-target-column row with
  its confidence score, transform, and review status, and unmet required target columns
  are highlighted

#### Scenario: Status color meanings do not collide

- **WHEN** the status chips are rendered across the application
- **THEN** each semantic color maps to one meaning, and each chip's meaning is also
  readable from its text

#### Scenario: Motion is optional

- **WHEN** a viewer has requested reduced motion
- **THEN** animations are disabled or reduced and all content remains reachable
