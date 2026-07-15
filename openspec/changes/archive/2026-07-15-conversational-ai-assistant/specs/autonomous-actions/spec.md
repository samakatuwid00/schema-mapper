## ADDED Requirements

### Requirement: Configurable guarded autonomy tiers

The system SHALL support conservative autonomy tiers that the user can set per conversation.

#### Scenario: Default tier is propose-only

- **WHEN** a new conversation starts
- **THEN** the autonomy tier defaults to `propose_only`
- **AND** the agent proposes actions but does not execute mutating tools without confirmation

#### Scenario: User changes autonomy tier

- **WHEN** a user selects a different autonomy tier in the chat UI (`propose_only` or `auto_safe`)
- **THEN** the tier is saved to the conversation
- **AND** the tier is applied to subsequent tool calls

### Requirement: Propose-only behavior

In `propose_only` mode, the agent SHALL explain what it would do and wait for user confirmation before any tool that mutates state.

#### Scenario: Propose-only proposes not executes

- **WHEN** a user asks "heal this error" in propose_only mode
- **THEN** the agent responds with the proposed action and a confirmation prompt
- **AND** the agent does not execute until the user confirms

### Requirement: Auto-safe behavior

In `auto_safe` mode, the agent SHALL auto-execute only tools marked as `auto_safe` when extracted parameters validate and confidence is above the configured threshold.

#### Scenario: Auto-safe auto-executes allowlisted high-confidence action

- **WHEN** a user asks for an allowlisted safe action in auto_safe mode
- **AND** all extracted parameters validate
- **AND** confidence is above the configured threshold
- **THEN** the agent executes the action and reports the result

#### Scenario: Auto-safe defers low-confidence action

- **WHEN** a user asks for an action in auto_safe mode
- **AND** confidence is below the configured threshold
- **THEN** the agent returns a confirmation prompt instead of executing

#### Scenario: Auto-safe defers non-allowlisted action

- **WHEN** a user asks for an action whose tool autonomy is `propose_only`
- **THEN** the agent returns a confirmation prompt instead of executing

### Requirement: Destructive actions always require confirmation

Tools marked as `destructive` SHALL always require explicit human confirmation regardless of autonomy tier.

#### Scenario: Deploy still requires confirmation

- **WHEN** a user asks "deploy schools" in auto_safe mode
- **THEN** the agent shows a confirmation prompt because deploy is destructive
- **AND** no deploy job is enqueued until the user confirms

### Requirement: Auto-all is not available in MVP

The MVP SHALL NOT expose an `auto_all` autonomy tier.

#### Scenario: Auto-all value is rejected

- **WHEN** the frontend or API submits `auto_all` as an autonomy tier
- **THEN** the backend rejects it with a validation error

### Requirement: Autonomy allowlist

Each tool definition SHALL declare its autonomy level: `propose_only`, `auto_safe`, or `destructive`.

#### Scenario: New tool defaults safe

- **WHEN** a new tool is registered without an explicit autonomy level
- **THEN** it defaults to `propose_only`

### Requirement: Autonomy audit trail

Every tool execution SHALL be recorded in the conversation history and audit log with the autonomy mode that was active at the time.

#### Scenario: Audit records autonomy mode

- **WHEN** a tool executes in auto_safe mode
- **THEN** the audit entry includes `{"autonomy": "auto_safe", "auto_executed": true}`
