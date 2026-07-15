# conversation-persistence Specification

## Purpose
TBD - created by archiving change conversational-ai-assistant. Update Purpose after archive.

## Requirements

### Requirement: Conversation storage in PostgreSQL

Conversations SHALL be stored in the `integration.agent_conversation` table with messages as JSONB and autonomy tier as a constrained text field.

#### Scenario: Message is persisted

- **WHEN** a user sends a message and receives a response
- **THEN** both the user message and agent response are appended to the conversation's messages array in the database

#### Scenario: Conversation created on first message

- **WHEN** a user sends a message without a conversation_id
- **THEN** a new conversation row is created with gen_random_uuid() ID, user_id from the authenticated session, `autonomy_tier='propose_only'`, and an empty title

#### Scenario: Unsupported autonomy tier rejected

- **WHEN** a conversation create or update attempts to store an unsupported autonomy tier
- **THEN** the database or API validation rejects it

### Requirement: Message format

Each message entry in the JSONB array SHALL contain role, content, optional tool_calls and tool_results, and a created_at timestamp.

#### Scenario: User message stored

- **WHEN** a user sends a message
- **THEN** the stored entry has `{"role": "user", "content": "hello", "created_at": "..."}`

#### Scenario: Agent message with tool call stored

- **WHEN** the agent executes a tool
- **THEN** the stored entry has `{"role": "assistant", "content": "response text", "tool_calls": [...], "tool_results": [...], "created_at": "..."}`

### Requirement: Title auto-generation

The conversation title SHALL be auto-generated from the first user message.

#### Scenario: Title from first message

- **WHEN** a conversation's first message is "what's blocking deploy for schools?"
- **THEN** the conversation title is set to "What's blocking deploy for schools?" truncated to 120 characters

### Requirement: Context resumption

When a user re-opens a previous conversation, the agent SHALL load the full message history and resume with the same persisted context.

#### Scenario: Previous conversation resumes

- **WHEN** a user selects a previous conversation from the history list and sends a new message
- **THEN** the agent loads the full message history and uses it as context for the new message

### Requirement: No PII in storage

Conversation messages SHALL never contain row values from source or target databases. Only schema metadata, identifiers, and agent action summaries may be stored.

#### Scenario: Tool results contain no row values

- **WHEN** a tool returns results that include column values
- **THEN** the agent filters out row values before storing the result in conversation history

### Requirement: Conversation retention limit

The system SHALL cap stored conversations per user at a configurable limit (default 100), deleting the oldest when the limit is exceeded.

#### Scenario: Old conversation pruned

- **WHEN** a user's conversation count exceeds the retention limit
- **THEN** the oldest conversation is automatically deleted on the next new conversation creation

### Requirement: Searchable persisted conversations

Persisted agent conversations SHALL be searchable by safe text fields for the authenticated user.

#### Scenario: Search uses redacted content

- **WHEN** the system searches messages
- **THEN** it searches only persisted, already-redacted conversation content
- **AND** it does not query raw source or target row values

### Requirement: Bulk delete persisted conversations

Persisted agent conversations SHALL support bulk deletion scoped to the authenticated user.

#### Scenario: Bulk delete is scoped

- **WHEN** a user bulk deletes conversation ids
- **THEN** the delete statement is constrained by that user's id
- **AND** conversations for other users remain unchanged
