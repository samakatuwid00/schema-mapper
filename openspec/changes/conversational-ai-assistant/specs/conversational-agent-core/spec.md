## ADDED Requirements

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

- **WHEN** a user asks for an unsupported task such as broad schema-swap apply
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
