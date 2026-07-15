# conversational-agent-core Specification

## Purpose
TBD - created by archiving change conversational-ai-assistant. Update Purpose after archive.

## Requirements

### Requirement: Intent-based message routing

The conversational agent SHALL classify each user message into one of the supported intents and route to the corresponding tool handler.

#### Scenario: Recognized intent dispatches to tool

- **WHEN** a user sends a message matching a known intent such as "what's blocking deploy for schools?"
- **THEN** the system classifies the intent
- **AND** calls the corresponding tool with extracted parameters

#### Scenario: Unrecognized intent asks for clarification

- **WHEN** the classifier confidence is below 0.5 for all known intents
- **THEN** the agent responds with a clarification message listing supported actions

### Requirement: MVP supported intents

The MVP SHALL support a narrow set of operator-focused intents before broader workflow automation.

#### Scenario: MVP intent list is registered

- **WHEN** the conversation runtime starts
- **THEN** it registers `check_status`, `summarize_proposal`, `explain_blocker`, `show_schema`, `deploy_guidance`, `explain_dilemma`, and `onboard_table`

#### Scenario: Non-MVP intent is deferred

- **WHEN** a user asks for an unsupported task such as a backup restore
- **THEN** the agent explains that the action is not supported in chat yet
- **AND** points the user to the existing dashboard or CLI workflow

### Requirement: Tool registry with typed definitions

The agent SHALL maintain a registry of callable tools, each with a name, description, JSON Schema for parameters, handler function, and autonomy level.

#### Scenario: Tool is defined with schema

- **WHEN** a tool is registered
- **THEN** its parameter schema is validated against incoming user-extracted parameters before the handler is called

#### Scenario: Invalid parameters return error

- **WHEN** extracted parameters fail schema validation
- **THEN** the agent returns a descriptive error message
- **AND** does not call the handler

### Requirement: Conversation context management

The agent SHALL maintain a conversation context object that includes message history, current workflow state if any, autonomy tier, and page-aware context from the frontend.

#### Scenario: Context limits prevent unbounded growth

- **WHEN** a conversation exceeds MAX_MESSAGES entries
- **THEN** older messages are summarized into a condensed context entry
- **AND** the summary remains schema-only with no row values

### Requirement: Heuristic/offline template fallback

When no LLM provider is configured, a provider times out, quota is exhausted, or all providers fail, the agent SHALL use template-based responses keyed by intent name.

#### Scenario: Offline mode uses templates

- **WHEN** `LLM_PROVIDER_ORDER` is set to `heuristic` or all LLM providers fail
- **THEN** the agent responds with pre-written template text for the detected intent, filled with available data

#### Scenario: Free-tier quota exhaustion degrades gracefully

- **WHEN** an LLM provider returns a rate-limit or quota error
- **THEN** the agent falls back to the next configured provider or heuristic templates
- **AND** returns a useful response rather than failing the chat request

### Requirement: LLM prompt budget and privacy guardrails

The agent SHALL cap LLM prompt/output size and SHALL send only schema metadata, IDs, statuses, and action summaries to the LLM.

#### Scenario: Prompt excludes row values

- **WHEN** a tool result includes source or target row values
- **THEN** those values are redacted before any prompt is built

#### Scenario: Prompt budget is enforced

- **WHEN** the conversation context exceeds the configured token budget
- **THEN** the agent summarizes or drops older context before calling the LLM

### Requirement: Response formatting

The agent SHALL return structured responses that include the natural-language reply and any structured result data for the frontend to render.

#### Scenario: Tool call triggers confirmation

- **WHEN** an intent routes to a tool whose autonomy level requires approval
- **THEN** the response includes `tool_call` data with the tool name, extracted parameters, and a confirmation prompt

### Requirement: Operator diagnostic tools

The agent SHALL provide read-only diagnostic tools for common operator incidents: job status, deploy failure explanation, and deployed-entity delivery state.

#### Scenario: Job status explains partial progress

- **WHEN** a user asks about a job id or the latest worker job
- **THEN** the agent returns the job type, status, progress, error message if present, and any per-entity failures available in the job result or event log

#### Scenario: Deployed entity has no target data

- **WHEN** a user asks why a deployed entity has no rows in the target database
- **THEN** the agent reports the entity deployment status, target tables, target row counts when readable, latest related refresh job, and next recommended diagnostic step

#### Scenario: Deploy error is explained as mapping repair

- **WHEN** a user provides a deploy error such as `required but no source column maps to it`
- **THEN** the agent extracts missing target columns and suggests concrete mapping actions without applying them

#### Scenario: Mapping repairs remain gated

- **WHEN** a user asks the agent to add or reject a mapping
- **THEN** the agent prepares a typed tool call and requires confirmation before changing review data

#### Scenario: Duplicate-key repair is diagnosed before mutation

- **WHEN** a refresh fails with a duplicate target primary key
- **THEN** the agent can inspect whether the target row exists, whether another crosswalk already claims it, and whether the target id comes from the entity's source primary key
- **AND** the agent only offers the repair tool when ownership is safe to record

#### Scenario: Duplicate-key repair requires confirmation

- **WHEN** the user asks the agent to repair a safe duplicate-key ownership gap
- **THEN** the agent requires confirmation before writing the missing central crosswalk row

#### Scenario: Refresh failure repair plan is read-only

- **WHEN** a refresh job succeeds partially or reports failed entities
- **THEN** the agent can produce a per-entity repair plan that classifies each failure and lists safe next actions
- **AND** the plan SHALL NOT mutate target data, mapping reviews, or crosswalk state

#### Scenario: Unsafe failures still name exact review actions

- **WHEN** a refresh failure cannot be auto-repaired but maps to a specific field-review row
- **THEN** the agent includes that proposal/review id and may suggest a separate confirmation-gated exact-row reject action

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
