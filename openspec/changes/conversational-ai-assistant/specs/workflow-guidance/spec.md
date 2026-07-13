## ADDED Requirements

### Requirement: MVP workflow state machines

The MVP SHALL define state machines for onboarding and deploy guidance. Drift resolution and schema swap guidance are later phases, not required for the MVP.

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

### Requirement: Later workflow requests are deferred

The agent SHALL respond safely when the user asks for drift resolution or schema swap guidance before those workflows are implemented in chat.

#### Scenario: Agent defers schema swap

- **WHEN** a user asks "I need to swap the target schema"
- **THEN** the agent explains that chat-guided schema swap is not available yet
- **AND** points the user to the existing schema-swap dashboard or CLI flow
