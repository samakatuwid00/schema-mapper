# workflow-guidance Specification

## Purpose
TBD - created by archiving change conversational-ai-assistant. Update Purpose after archive.

## Requirements

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

### Requirement: Operator repair guidance flow

The agent SHALL turn deploy and delivery failures into a short diagnosis plus the next safe action.

#### Scenario: Missing required mapping guidance

- **WHEN** deploy validation reports unmapped required target columns
- **THEN** the agent lists each missing target column and suggests adding source-to-target mappings or rejecting incorrect suggestions through gated tools

#### Scenario: Refresh succeeded but some entities failed

- **WHEN** a refresh job reports fewer successful entities than requested
- **THEN** the agent identifies which entity failed when the job result contains that detail and recommends inspecting or retrying that entity only

#### Scenario: Duplicate key becomes crosswalk repair guidance

- **WHEN** a failed refresh result contains a duplicate target primary-key error
- **THEN** the agent explains that deploy status does not imply data delivery and can prepare a gated crosswalk reconciliation before retrying that entity

#### Scenario: Job-level repair checklist

- **WHEN** an operator asks how to fix a failed refresh job
- **THEN** the agent returns a repair checklist per failed entity, including gated tool calls only for actions proven safe and manual review steps for unsafe or ambiguous failures

#### Scenario: Reference-table match failure identifies mapping review

- **WHEN** a failed refresh result reports "no row in reference table <table> matches {<column>: <value>}"
- **THEN** the agent identifies the missing reference table/column
- **AND** includes the accepted mapping proposal/review id when one can be found for that target column

#### Scenario: Proposal summary supports repair lookup

- **WHEN** an operator summarizes a proposal while repairing a refresh failure
- **THEN** the agent includes accepted field review ids and source-to-target mapping names
- **AND** does not include source or target row values

#### Scenario: Failed delivery can reopen suspect mappings

- **WHEN** a refresh failure points to a missing reference row and the exact target-column mapping is absent
- **THEN** the agent identifies suspect accepted mappings on the same target table such as non-id source columns mapped to target id columns
- **AND** offers a confirmation-gated action to reopen the suspect field review in the normal review queue instead of silently remapping it

#### Scenario: Job inspection includes repair handles

- **WHEN** an operator inspects a refresh job with failed entities
- **THEN** the agent includes any inferred proposal id, review id, and confirmation-gated repair command from the repair planner
- **AND** keeps the answer read-only unless the operator confirms a separate repair command

#### Scenario: Chat help lists supported commands

- **WHEN** an operator sends "--help" or asks for chat commands
- **THEN** the agent returns a concise command reference including read-only diagnostics and confirmation-gated repair commands

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

### Requirement: Deploy failure repair workflow

The assistant SHALL guide operators from a failed deploy job to mapping repair, review, approval, and redeploy.

#### Scenario: Failed deploy job becomes repair checklist

- **WHEN** an operator asks how to fix a failed `deploy_lrmis` job
- **THEN** the assistant identifies the failed proposal when available
- **AND** returns a checklist covering missing mappings, mapping review, approval, and redeploy

#### Scenario: Proposal disappeared from queue

- **WHEN** the failed proposal is not visible in the normal review queue
- **THEN** the assistant still provides a direct open-proposal action for `/mappings/{proposal_id}`
- **AND** explains that approved proposals may leave the review queue while remaining accessible by URL

#### Scenario: Redeploy remains explicit

- **WHEN** a mapping repair is applied
- **THEN** the assistant recommends reviewing/approving the proposal and explicitly starting deploy again
- **AND** no deploy job is enqueued without the user's separate confirmation
