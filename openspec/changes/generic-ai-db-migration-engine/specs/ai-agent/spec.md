## ADDED Requirements

### Requirement: AI-powered migration planning

The system SHALL embed a provider-agnostic `MigrationAgent` (running over the free-tier multi-provider mapping layer, not tied to one vendor) that generates a migration plan from source and target **schema metadata** — never row values — proposing mappings, identifying risks, and estimating effort.

#### Scenario: Agent produces a migration plan

- **WHEN** an admin runs `sync-engine plan` with a configured source and target
- **THEN** the agent returns a structured plan with table-level mapping, column-level mapping, FK relationships, risk flags (unmappable columns, type loss, missing indexes), and confidence scores — built from schema metadata only

### Requirement: Interactive agent session

The system SHALL provide an interactive agent session (`sync-engine agent`) in which the admin converses with the agent to plan, review, and resolve a migration step by step, with the agent proposing actions the admin accepts, edits, or rejects.

#### Scenario: Admin drives a migration interactively

- **WHEN** an admin starts `sync-engine agent` for a configured source/target
- **THEN** the agent walks the admin through discovery, proposed mappings, and unresolved dilemmas interactively, applying only the actions the admin confirms

### Requirement: Interactive guided migration

The agent SHALL support a guide mode that presents dilemmas (unmapped columns, FK violations, type mismatches) as natural-language options for the admin to choose from.

#### Scenario: Guide is blocked on an unmapped column

- **WHEN** a deploy validation fails because a target column has no source mapping
- **THEN** the agent presents options: (a) auto-suggest a mapping based on name/type similarity, (b) skip the column, (c) prompt the admin to define the mapping — and applies the chosen option

### Requirement: Gated self-healing

The agent SHALL support a self-heal mode on delivery errors that reviews the error + row + mapping and **proposes** a fix. Applying the fix automatically SHALL be opt-in and off by default (especially on free-tier models); otherwise the agent quarantines and escalates.

#### Scenario: Healer proposes a type-mismatch fix

- **WHEN** a delivery fails because the source sent `"123"` (string) but the target expects `INTEGER`, and autonomous-apply is disabled
- **THEN** the healer proposes casting the value and quarantines the row for admin confirmation rather than applying the fix unattended

#### Scenario: Healer applies only when autonomous-apply is enabled

- **WHEN** the same failure occurs and autonomous-apply is explicitly enabled
- **THEN** the healer applies the cast, retries, and logs the fix pattern for reuse

### Requirement: Audit trail for agent actions

All agent actions (plan created, guide answer selected, heal proposed/applied) SHALL be recorded in `integration.onboarding_audit`.

#### Scenario: Agent action is audited

- **WHEN** the agent proposes or applies a heal for a delivery error
- **THEN** an audit row is inserted with `action = 'agent_heal'`, `details` containing the error context and the proposed/applied fix, and `performed_by = 'agent'`
