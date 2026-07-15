## ADDED Requirements

### Requirement: Full-screen conversation mode

The chat panel SHALL provide a control that expands the docked sidebar into a full-screen conversation view and collapses it back to the docked panel, without losing conversation state.

#### Scenario: User expands to full screen

- **WHEN** the user clicks the expand control in the panel header
- **THEN** the conversation view fills the viewport with a centered, width-constrained reading column
- **AND** the active conversation, its messages, the unsent draft, and scroll position are preserved

#### Scenario: User collapses back to the docked panel

- **WHEN** the user clicks the collapse control (or presses Escape) while in full-screen mode
- **THEN** the view returns to the docked right-hand sidebar
- **AND** the same conversation, draft, and scroll position are preserved

#### Scenario: Streaming continues across a mode switch

- **WHEN** the user toggles full-screen mode while a response is streaming
- **THEN** streaming continues uninterrupted and tokens keep rendering in the new layout

### Requirement: Animated assistant presence

The chat panel SHALL render an animated assistant figure that reflects the assistant's state, and SHALL suppress motion when the user prefers reduced motion.

#### Scenario: Idle animation

- **WHEN** the assistant is idle and awaiting input
- **THEN** the assistant figure shows a subtle continuous idle animation (for example a breathing pulse or glow)

#### Scenario: Active animation while streaming

- **WHEN** a response is streaming or a tool call is running
- **THEN** the assistant figure switches to a distinct active "thinking" animation
- **AND** it returns to the idle animation when streaming completes

#### Scenario: Reduced motion is honored

- **WHEN** the user's system requests reduced motion (`prefers-reduced-motion: reduce`)
- **THEN** the figure renders in a static state with no looping animation, while still visually distinguishing idle from active

### Requirement: Markdown message rendering

The chat panel SHALL render settled assistant messages as formatted markdown, and SHALL render the content as sanitized output that cannot inject raw HTML or scripts.

#### Scenario: Formatted markdown renders

- **WHEN** a settled assistant message contains markdown (headings, bulleted or numbered lists, bold or italic text, inline code, or fenced code blocks)
- **THEN** the panel renders those elements as formatted content rather than raw markdown characters

#### Scenario: Code blocks are readable

- **WHEN** an assistant message contains a fenced code block
- **THEN** the block renders in a monospace, visually distinct container that wraps or scrolls rather than overflowing the panel

#### Scenario: Streaming text stays plain until settled

- **WHEN** a response is still streaming
- **THEN** in-progress tokens render as plain text with the streaming cursor
- **AND** markdown formatting is applied once the message settles

#### Scenario: Untrusted content is sanitized

- **WHEN** an assistant message contains raw HTML or a script-like payload
- **THEN** it is rendered as inert text and no embedded HTML or script executes

### Requirement: Futuristic visual treatment

The chat panel SHALL present a modern, futuristic visual design that reuses the existing theme tokens, and message roles SHALL remain visually distinguishable.

#### Scenario: Panel uses the existing theme system

- **WHEN** the redesigned panel renders
- **THEN** its colors, glow, elevation, and typography derive from the existing CSS design tokens (accent, glow, elevation, font variables) rather than introducing a separate color system

#### Scenario: Roles remain distinguishable

- **WHEN** a conversation contains both user and assistant messages
- **THEN** each role is visually distinct (alignment, avatar, or container treatment) so the reader can tell who said what at a glance

#### Scenario: Restyle preserves existing controls

- **WHEN** the redesigned panel renders
- **THEN** the autonomy selector, conversation history, new-chat, inline Approve/Cancel, and message composer remain present and operable
