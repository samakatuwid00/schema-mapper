## ADDED Requirements

### Requirement: Agent as first-class interaction mode

The `MigrationAgent` SHALL expose a `converse()` entry point that accepts a natural language message and conversation context, classifies intent, routes to the appropriate capability (plan/guide/heal/status/workflow), and returns a structured response.

#### Scenario: Converse routes to plan

- **WHEN** a user says "map table schools to the target" via converse()
- **THEN** the agent calls `plan(source_table, target_schema)` and returns the graded plan in natural language

#### Scenario: Converse routes to guide

- **WHEN** a user says "what should I do about the unmapped name column?" via converse()
- **THEN** the agent creates a Dilemma and calls `guide()`, returning the recommended resolution in natural language

#### Scenario: Converse routes to heal

- **WHEN** a user says "fix this delivery error" via converse()
- **THEN** the agent creates the heal proposal and returns the suggested action in natural language

### Requirement: Agent session context

`MigrationAgent` SHALL accept an optional `conversation_id` and `context` dict that the conversation layer uses for page-aware disambiguation and workflow state tracking.

#### Scenario: Context disambiguates entity reference

- **WHEN** a user says "deploy this" and the context includes `{"entity": "schools"}`
- **THEN** the agent resolves "this" to "schools" and proceeds with the deploy guidance flow

### Requirement: Existing programmatic interface unchanged

The `plan()`, `guide()`, and `heal()` methods SHALL remain callable with the same signatures they had before this change, and SHALL produce the same return types.

#### Scenario: Existing calls continue to work

- **WHEN** the worker calls `agent.heal(error_text)` (no conversation context)
- **THEN** it returns a `HealProposal` as before, with no change in behavior
