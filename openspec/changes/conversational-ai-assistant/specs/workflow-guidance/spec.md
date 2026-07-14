## ADDED Requirements

### Requirement: MVP workflow state machines

The MVP SHALL define state machines for onboarding and deploy guidance. Drift resolution and schema swap guidance land as later phases (§8) with their own state machines once the MVP is in place.

#### Scenario: Workflow loads next step

- **WHEN** the user completes a step in a supported workflow
- **THEN** the agent automatically suggests the next valid step with a brief explanation

#### Scenario: User can request current workflow status

- **WHEN** a user asks "where are we in the onboarding?"
- **THEN** the agent responds with the current step, completed steps, and remaining steps

### Requirement: Onboarding guidance flow

The agent SHALL provide a guided walkthrough of the onboarding process: discover -> propose -> review -> deploy -> backfill.

#### Scenario: Agent starts onboarding

- **WHEN** a user says "I want to onboard a new table"
- **THEN** the agent asks which source table to onboard
- **AND** calls the appropriate discover/propose service only after required parameters are known
- **AND** presents results before asking for the next step

#### Scenario: Agent handles review step

- **WHEN** the propose step returns mappings needing review
- **THEN** the agent explains each low-confidence mapping
- **AND** offers options: accept as-is, suggest alternatives, or let the user specify manually

### Requirement: Deploy guidance flow

The agent SHALL guide the user through deploying a proposal to the target, including handling blocked deployments.

#### Scenario: Agent checks deploy readiness

- **WHEN** a user says "deploy entity X"
- **THEN** the agent checks deploy readiness and coverage
- **AND** if blocked, presents guidance options from the existing service layer

#### Scenario: Deploy requires confirmation

- **WHEN** deploy guidance determines that a deploy job can be enqueued
- **THEN** the agent presents the deploy action as a confirmation prompt
- **AND** does not enqueue the deploy until the user confirms

### Requirement: Drift resolution guidance flow

The agent SHALL guide the user through drift resolution: list drift reports, review the diff, re-map, and apply — with the apply step destructive-gated.

#### Scenario: Drift question lists reports and suggests the flow

- **WHEN** a user asks "any drift lately?"
- **THEN** the agent lists the recorded drift reports with impacted entities
- **AND** suggests the next step of the drift workflow

#### Scenario: Applying drift resolution requires confirmation

- **WHEN** a user asks to resolve drift
- **THEN** the agent presents `resolve_drift` as a confirmation prompt (destructive) and does not execute until confirmed

### Requirement: Schema-swap guidance flow

The agent SHALL guide the user through a target schema swap: read-only dry-run diff, re-map review, then a confirmed destructive apply that additionally requires the same typed target-database token as the CLI.

#### Scenario: Swap request runs the read-only preview

- **WHEN** a user asks "I need to swap the target schema"
- **THEN** the agent runs the read-only swap dry-run, reports affected entities, and suggests the next workflow step

#### Scenario: Swap apply is double-gated

- **WHEN** a user confirms a `swap_target_apply` tool call without the typed target-database token in its parameters
- **THEN** the handler refuses with the expected token named, and nothing is changed

### Requirement: Recovery requests are deferred

The agent SHALL respond safely when the user asks for backup recovery, which remains dashboard/CLI-only.

#### Scenario: Agent defers backup recovery

- **WHEN** a user asks "restore a backup"
- **THEN** the agent explains that chat-guided recovery is not available
- **AND** points the user to the Recovery page or `scripts/recover.py`
