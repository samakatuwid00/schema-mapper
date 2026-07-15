# agent-sidebar-ui Specification

## Purpose
TBD - created by archiving change conversational-ai-assistant. Update Purpose after archive.

## Requirements

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

### Requirement: Searchable conversation history

The chat panel SHALL let users search their conversation history by title and safe persisted message text.

#### Scenario: User searches history

- **WHEN** the user types a query in the history search box
- **THEN** the history list filters to matching conversations owned by that user
- **AND** empty results show a clear empty state

### Requirement: Bulk conversation deletion

The chat panel SHALL let users select multiple history conversations and delete them after confirmation.

#### Scenario: User deletes selected conversations

- **WHEN** the user selects multiple conversations and clicks bulk delete
- **THEN** the UI asks for confirmation with the number of selected conversations
- **AND** confirmed deletion removes those conversations from the list

#### Scenario: Current conversation deleted

- **WHEN** the selected bulk delete includes the currently loaded conversation
- **THEN** the chat panel starts a fresh conversation after deletion succeeds

### Requirement: Usable autonomy mode control

The chat panel SHALL present autonomy modes with human-readable labels and descriptions while preserving the backend enum values.

#### Scenario: Autonomy labels are understandable

- **WHEN** the autonomy control renders
- **THEN** it shows labels such as "Ask first" for `propose_only` and "Auto safe" for `auto_safe`
- **AND** each option explains what the assistant may do in that mode

#### Scenario: Unsupported autonomy remains unavailable

- **WHEN** the autonomy control renders
- **THEN** `auto_all` is not available as an option

### Requirement: Assistant and job inspection coexist

The full-screen assistant SHALL allow the user to inspect a specific job while continuing the repair conversation.

#### Scenario: Failed job launches repair chat

- **WHEN** the user clicks "Repair with Assistant" on a failed job card
- **THEN** the assistant opens with that job pinned as context
- **AND** the user can see the job id, status, error, and repair conversation together

#### Scenario: Full-screen mode preserves job context

- **WHEN** the assistant enters full-screen mode from a failed job repair flow
- **THEN** the selected job remains visible through a pinned card, split rail, or coordinated overlay

### Requirement: Repair answer action chips

The chat panel SHALL render structured repair actions as buttons or chips when the assistant provides them.

#### Scenario: Open proposal chip

- **WHEN** a repair answer includes an open-proposal action
- **THEN** the UI renders a control that navigates to `/mappings/{proposal_id}`

#### Scenario: Gated repair chip

- **WHEN** a repair answer includes a confirmation-gated mapping repair action
- **THEN** the UI renders an approval control consistent with existing guarded tool confirmation behavior

### Requirement: Accessible slide motion

The assistant UI SHALL use smooth slide transitions for major panel state changes while honoring reduced-motion preferences.

#### Scenario: Panels slide during normal motion

- **WHEN** the assistant opens, history opens, full-screen expands, or job repair context appears
- **THEN** the UI uses a short slide transition that clarifies the spatial change

#### Scenario: Reduced motion disables sliding

- **WHEN** the user's system requests reduced motion
- **THEN** sliding and looping animations are removed or simplified while all controls remain usable
