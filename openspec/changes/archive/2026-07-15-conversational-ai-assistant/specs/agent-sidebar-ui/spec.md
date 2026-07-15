## ADDED Requirements

### Requirement: Collapsible sidebar chat panel

The admin web UI SHALL include a collapsible chat panel on the right side of the Shell layout, visible across all pages.

#### Scenario: Chat panel toggles open and closed

- **WHEN** the user clicks the chat toggle button in the topbar
- **THEN** the chat panel slides open or closed with a smooth transition
- **AND** state persists across page navigation within the session

#### Scenario: Chat panel is closed by default

- **WHEN** a user first loads the admin UI
- **THEN** the chat panel is closed
- **AND** a small indicator shows available agent help

### Requirement: Page-aware context injection

The chat panel SHALL automatically send the current page route and relevant entity/proposal IDs with each message.

#### Scenario: Context sent with message

- **WHEN** a user sends a message from the Mapping Review page for proposal 42
- **THEN** the frontend includes `{"page": "/mappings/42", "context": {"proposal_id": 42}}` in the API call

#### Scenario: Context updates on navigation

- **WHEN** the user navigates to a different page
- **THEN** the chat panel's context is updated to reflect the new page without losing the conversation

### Requirement: Streaming response rendering

The chat panel SHALL render SSE stream events progressively: text tokens as they arrive, tool call indicators, tool results, and confirmation prompts.

#### Scenario: Streaming text renders progressively

- **WHEN** the agent sends `token` events
- **THEN** each token is appended to the current message in real time

#### Scenario: Final stream frame is rendered before reload

- **WHEN** the backend sends the final `done` frame with assistant content
- **THEN** the chat panel renders that content in the current conversation immediately, even if the frame uses CRLF newlines or arrives in the final network buffer

#### Scenario: Tool call shows progress indicator

- **WHEN** the agent sends a `tool_call` event
- **THEN** the UI shows a compact progress indicator while the tool executes

#### Scenario: Confirmation prompt renders inline

- **WHEN** the agent sends a `tool_call` with `requires_confirmation: true`
- **THEN** the UI shows Approve and Cancel controls inline in the chat

### Requirement: Guarded autonomy selector

The chat panel SHALL allow the user to choose only supported MVP autonomy tiers.

#### Scenario: User selects auto-safe

- **WHEN** the user changes the autonomy selector to `auto_safe`
- **THEN** subsequent messages include that tier
- **AND** the backend persists it to the conversation

#### Scenario: Auto-all is not shown

- **WHEN** the chat panel renders the autonomy selector
- **THEN** `auto_all` is not available as an option

### Requirement: Conversation history list

The chat panel SHALL display a list of previous conversations and allow switching between them.

#### Scenario: User switches conversation

- **WHEN** the user clicks a previous conversation from the history list
- **THEN** the chat panel loads and displays that conversation's messages

#### Scenario: User deletes a conversation

- **WHEN** the user clicks the delete button on a conversation
- **THEN** the system confirms deletion and removes it from the list
