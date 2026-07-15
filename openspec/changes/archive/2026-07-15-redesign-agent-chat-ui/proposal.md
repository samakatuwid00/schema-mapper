## Why

The migration assistant works but looks and reads like a plain debug panel: a fixed 22rem sidebar, a static bot icon, and assistant replies dumped as raw `pre-wrap` text with no markdown, no headings, no code formatting. Operators reading status, blockers, and SQL in the chat get a wall of text that is hard to scan, and there is no way to expand the conversation when a reply is long. This change modernizes the presentation layer only — no backend, streaming, autonomy, or persistence behavior changes.

## What Changes

- **Futuristic visual restyle** of the assistant panel, message rows, and composer, reusing the existing accent + glow design tokens (`--accent`, `--glow-live`, `--elev-*`) — no new color system.
- **Animated assistant figure**: the assistant's avatar becomes a small moving presence with an idle "breathing"/pulse state and an active "thinking" state while a response streams. CSS/SVG only; honors `prefers-reduced-motion`.
- **Rich markdown rendering** for assistant messages: headings, lists, bold/italic, inline code, and fenced code blocks render as formatted content instead of raw text. User messages and streaming tokens stay plain until the message settles.
- **Full-screen mode**: a toggle expands the sidebar into a full-screen conversation view (wider, centered reading column) and back to the docked panel, preserving the active conversation, draft, and scroll position.
- Existing behaviors preserved unchanged: page-aware context injection, SSE streaming, inline Approve/Cancel confirmation, autonomy selector, conversation history.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `agent-sidebar-ui`: adds requirements for a full-screen conversation mode, an animated assistant presence, markdown message rendering, and a futuristic visual treatment — layered on top of the existing sidebar requirements without changing streaming, context, autonomy, or history behavior.

## Impact

- **Frontend only**: `web/src/components/AgentSidebar.tsx`, `web/src/components/ChatMessage.tsx`, `web/src/styles.css`. New small presentational components (animated avatar, markdown renderer wrapper) may be added under `web/src/components/`.
- **Dependency**: one lightweight, well-maintained markdown renderer (e.g. `react-markdown`) added to `web/package.json`, with sanitization so assistant output cannot inject HTML.
- **No changes** to `src/agent/*`, `src/admin_api/agent_chat.py`, the SSE contract, `useAgentChat`, or conversation persistence.
- **Tests**: extend `web/src/components/__tests__/AgentSidebar.test.tsx`; add coverage for markdown rendering, full-screen toggle, and reduced-motion.
