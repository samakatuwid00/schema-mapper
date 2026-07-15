## ADDED Requirements

### Requirement: Deploy job repair context

The agent SHALL resolve pasted deploy job messages into a repair context when the message contains a job UUID.

#### Scenario: Pasted deploy job resolves proposal id

- **WHEN** a user pastes a failed `deploy_lrmis` job message containing an `admin_job.id`
- **AND** that job stores `params.proposal_id`
- **THEN** the agent returns the job id, job type, status, proposal id, and parsed deploy error summary
- **AND** the agent does not ask the user to manually find the proposal id

#### Scenario: Job id cannot be resolved

- **WHEN** a user pastes a failed deploy message with a UUID that does not match a visible job
- **THEN** the agent still parses the error text for missing required target columns
- **AND** asks for a proposal id or suggests opening the failed job details

### Requirement: Missing required mapping repair drafts

The agent SHALL convert deploy validation errors for unmapped required target columns into a repair draft without mutating mapping data until confirmation.

#### Scenario: Required id columns produce fan-out draft

- **WHEN** a deploy error reports missing target columns such as `station_name.id`
- **AND** the proposal includes a source column named `id`
- **THEN** the agent drafts a proposed mapping from `id` to each missing `*.id` target column
- **AND** labels the draft as confirmation-gated

#### Scenario: Non-id missing columns require manual choice

- **WHEN** a deploy error reports a missing required target column that is not `id`
- **THEN** the agent lists the target column as requiring a source-column choice unless a deterministic source-column match exists

#### Scenario: Repair mutation requires confirmation

- **WHEN** the user accepts a drafted mapping repair
- **THEN** the agent uses a typed confirmation-gated tool to add the mapping rows
- **AND** the result includes the proposal id, added mappings, skipped mappings, and next recommended action

### Requirement: Structured repair actions

The agent SHALL include structured action metadata in repair responses so the frontend can render action chips.

#### Scenario: Repair answer includes open proposal action

- **WHEN** a deploy repair response has a proposal id
- **THEN** the structured response includes an action to open `/mappings/{proposal_id}`

#### Scenario: Repair answer includes gated mapping action

- **WHEN** the agent drafts missing mapping additions
- **THEN** the structured response includes a confirmation-gated action with the target proposal and mapping rows

