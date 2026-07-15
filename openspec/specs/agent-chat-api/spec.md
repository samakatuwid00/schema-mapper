# agent-chat-api Specification

## Purpose
TBD - created by archiving change conversational-ai-assistant. Update Purpose after archive.

## Requirements

### Requirement: Chat endpoint with SSE streaming

The system SHALL expose `POST /api/agent/chat` that accepts a user message, optional conversation ID, and optional page context, and returns an SSE stream of typed events.

#### Scenario: Message sends successfully

- **WHEN** a user sends `POST /api/agent/chat` with `{"message": "hello", "conversation_id": "uuid"}`
- **THEN** the endpoint returns `text/event-stream` with typed events (`conversation`, `token`, `tool_call`, `tool_result`, `error`, `done`)

#### Scenario: New conversation starts without ID

- **WHEN** a user sends `POST /api/agent/chat` with only `{"message": "hello"}`
- **THEN** the system creates a new conversation
- **AND** the first SSE event includes the new conversation ID
- **AND** the system processes the message

#### Scenario: Page context is accepted

- **WHEN** a user sends `POST /api/agent/chat` with `{"message": "deploy this", "context": {"proposal_id": 42}}`
- **THEN** the context is validated and passed to the conversation layer for disambiguation

### Requirement: Conversation CRUD

The system SHALL support listing, reading, and deleting conversations scoped to the authenticated user.

#### Scenario: List conversations

- **WHEN** a user sends `GET /api/agent/conversations`
- **THEN** the endpoint returns a list of their conversations (id, title, created_at, updated_at, message_count, autonomy_tier)

#### Scenario: Read conversation

- **WHEN** a user sends `GET /api/agent/conversations/{id}`
- **THEN** the endpoint returns the full conversation with all messages

#### Scenario: Delete conversation

- **WHEN** a user sends `DELETE /api/agent/conversations/{id}`
- **THEN** the conversation is deleted and the endpoint returns `{"ok": true}`

#### Scenario: Unauthorized access returns 404

- **WHEN** a user requests a conversation belonging to another user
- **THEN** the endpoint returns 404

### Requirement: Simple stream recovery

The SSE stream SHALL recover from a dropped connection by reloading persisted conversation state. Exact replay of in-flight token events is not required in the MVP.

#### Scenario: Client reconnects after dropped stream

- **WHEN** the SSE connection drops
- **AND** the client reloads the conversation by ID
- **THEN** the server returns the latest persisted messages
- **AND** the client may retry the last user message if no assistant response was persisted

### Requirement: Audit logging

Every agent action triggered through the chat endpoint SHALL be audited via the existing audit system.

#### Scenario: Agent action is audited

- **WHEN** the agent executes a tool
- **THEN** an audit entry is written with actor=username, action="agent:TOOL_NAME", target_type="agent_conversation", target_id=conversation_id

### Requirement: Conversation search API

The agent conversation API SHALL support searching the authenticated user's conversations.

#### Scenario: Search conversations

- **WHEN** a user requests their conversations with a search query
- **THEN** the API returns only that user's conversations whose title or persisted redacted message text matches the query

#### Scenario: Search is user scoped

- **WHEN** another user's conversation matches the query
- **THEN** it is not returned and its existence is not revealed

### Requirement: Bulk conversation delete API

The agent conversation API SHALL support deleting multiple conversations owned by the authenticated user in one request.

#### Scenario: Bulk delete owned conversations

- **WHEN** a user submits a list of conversation ids they own for deletion
- **THEN** the API deletes those conversations and returns the number deleted

#### Scenario: Bulk delete does not leak foreign ids

- **WHEN** the request includes ids owned by another user or nonexistent ids
- **THEN** the API does not reveal which ids were foreign or nonexistent
- **AND** only conversations owned by the caller are deleted
