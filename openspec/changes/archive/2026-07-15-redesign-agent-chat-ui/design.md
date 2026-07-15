## Context

The migration assistant is a working feature delivered by the completed `conversational-ai-assistant` change. Its UI is three pieces:

- `web/src/components/AgentSidebar.tsx` — fixed 22rem right panel, mostly inline styles, header controls (new / history / close), autonomy selector, history list, message scroll area, composer.
- `web/src/components/ChatMessage.tsx` — role icon (static `Bot`/`User` from lucide), tool-call chips, message body rendered as raw `white-space: pre-wrap` text, inline Approve/Cancel.
- `web/src/styles.css` — a mature dark theme with design tokens already present: `--accent`/`--accent-strong`, `--glow-live`, `--elev-1..3`, `--radius*`, `--dur*`, `--ease-out`, status colors, Fira font stack.

Streaming, page-aware context, autonomy gating, and persistence come from `useAgentChat` and the backend SSE contract; this redesign does not touch them. The work is entirely presentational. Constraint: reuse the existing token system (no parallel color/spacing system), keep the change lean, and keep every existing control operable.

## Goals / Non-Goals

**Goals:**

- A modern, futuristic look for the panel, message rows, and composer built from existing tokens.
- An animated assistant figure with idle and active ("thinking") states that respects `prefers-reduced-motion`.
- Formatted, sanitized markdown rendering for settled assistant messages.
- A full-screen mode that expands and collapses while preserving conversation, draft, and scroll state.

**Non-Goals:**

- No backend, SSE, autonomy, or persistence changes.
- No change to `useAgentChat` semantics or the page-context payload shape.
- No new design-token system, theme, or global layout refactor.
- No rich composer (attachments, slash commands) — composer stays a single-line send.

## Decisions

### D1: Markdown via `react-markdown` + `remark-gfm`, no raw HTML

Render settled assistant content with `react-markdown` and `remark-gfm` (lists, tables, strikethrough, autolinks). `react-markdown` does **not** render raw HTML unless `rehype-raw` is explicitly added — so by omitting it, embedded HTML/script is rendered inert as text with no extra sanitizer needed. This satisfies the "untrusted content is sanitized" scenario with the smallest surface.

- Alternatives: (a) hand-rolled regex formatter — rejected, fragile and re-implements a solved problem; (b) `markdown-it` + DOMPurify — rejected, heavier and needs an explicit sanitize step. Two small, widely-used deps beat a bespoke parser.
- Fenced code blocks render into a monospace `--mono` container with `overflow-x: auto`; no syntax-highlighting dependency in this pass (keep it lean).

### D2: Stream plain, render markdown on settle

While `streaming` is true for the last message, keep rendering the live buffer as plain `pre-wrap` text plus the existing cursor — markdown-parsing a half-arrived string produces flicker and broken code fences. Once the message is settled (not the streaming tail), pass its content through the markdown renderer. User messages always render plain. This keeps token-by-token streaming visually stable and only formats final content.

### D3: Full-screen via a layout-mode flag on one component instance

Add `mode: "docked" | "fullscreen"` state inside `AgentSidebar`. The **same** component instance and the same `useAgentChat` state render in both modes; only the outer container's class/geometry changes (docked `aside` vs a fixed full-viewport overlay with a centered `max-width` reading column). Because the component does not unmount, messages, draft, `scrollRef` position, and an in-flight stream survive the toggle for free. Escape collapses when in full-screen. The topbar toggle continues to open/close; expand/collapse is a separate header control.

- Alternative: a separate route/page for full-screen — rejected, would remount, drop the stream and draft, and duplicate wiring.

### D4: Animated figure in CSS/SVG, state-driven, motion-safe

A small self-contained `AssistantAvatar` component renders an SVG orb/figure. State is derived from existing chat state: `active = streaming || pendingConfirm-running`, else `idle`. Idle = slow breathing pulse/glow loop; active = faster distinct "thinking" motion. All animation is CSS keyframes gated behind `@media (prefers-reduced-motion: no-preference)`; under `reduce` the element is static but still swaps idle/active styling (e.g. glow intensity) so state stays legible without motion. No animation library.

### D5: Styling moves to CSS classes; tokens only

The redesign introduces named classes (e.g. `.agent-panel`, `.agent-msg`, `.agent-avatar`, `.agent-markdown`) in `styles.css` instead of growing the inline-style blobs, so the futuristic treatment (glass surface, accent glow, elevation) is themeable and testable. Every value references an existing token; no new hex colors. Inline styles that encode layout geometry (full-screen vs docked) may remain inline where they are mode-dependent.

## Risks / Trade-offs

- **Markdown of a partial stream looks broken** → D2 defers formatting until settle; only plain text streams.
- **New dependencies (`react-markdown`, `remark-gfm`) increase bundle size** → both are small, tree-shakeable, and widely used; no highlighter added. Acceptable for an admin console.
- **Animation distracts or harms accessibility** → D4 gates all motion behind `prefers-reduced-motion` and keeps amplitude subtle.
- **Full-screen overlay traps focus or hides the app** → provide a visible collapse control plus Escape; overlay is dismissable and never blocks logout/navigation permanently since collapse restores the docked panel.
- **Regression in existing tests** → existing `AgentSidebar.test.tsx` asserts current DOM; update selectors as classes/markup change and add tests for the four new requirements.

## Migration Plan

1. Add `react-markdown` + `remark-gfm` to `web/package.json`.
2. Add CSS classes/keyframes to `styles.css` (tokens only).
3. Introduce `AssistantAvatar` and a markdown-render wrapper; refactor `ChatMessage` to use them (plain while streaming, markdown when settled).
4. Add `mode` state + expand/collapse control + Escape handling to `AgentSidebar`.
5. Update and extend Vitest coverage.

Rollback: revert the frontend commit and drop the two dependencies; backend is untouched, so no data or API rollback is involved.

## Open Questions

- Exact figure motif (orb vs abstract waveform) — decided during implementation; any choice must satisfy the idle/active/reduced-motion scenarios.
- Whether full-screen should also widen to a two-pane layout (history beside chat) later — out of scope now; current history popover is reused in both modes.
