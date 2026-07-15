## 1. Dependencies & tokens

- [x] 1.1 Add `react-markdown` and `remark-gfm` to `web/package.json` and install; confirm the build/typecheck still passes.
- [x] 1.2 Add redesign CSS classes and keyframes to `web/src/styles.css` (`.agent-panel`, `.agent-msg`, `.agent-avatar`, `.agent-markdown`, idle/active avatar keyframes) using existing tokens only — no new hex colors.
- [x] 1.3 Wrap all avatar/keyframe animation in `@media (prefers-reduced-motion: no-preference)`, with a static idle/active fallback under `reduce`.

## 2. Animated assistant figure

- [x] 2.1 Create `web/src/components/AssistantAvatar.tsx` rendering a CSS/SVG orb/figure with `state: "idle" | "active"` prop.
- [x] 2.2 Implement idle (breathing pulse/glow) and active (distinct "thinking") animations driven by the `state` prop.
- [x] 2.3 Verify reduced-motion: no looping animation, idle vs active still visually distinct (glow/opacity only).

## 3. Markdown message rendering

- [x] 3.1 Add a markdown render wrapper (`.agent-markdown`) using `react-markdown` + `remark-gfm`, with raw HTML disabled so untrusted content renders inert.
- [x] 3.2 Style fenced code blocks in a monospace (`--mono`) container with `overflow-x: auto`; style lists, headings, inline code, bold/italic to match the theme.
- [x] 3.3 In `ChatMessage.tsx`: render the streaming tail (last message while `streaming`) as plain `pre-wrap` + cursor; render settled assistant messages through the markdown wrapper; keep user messages plain.
- [x] 3.4 Preserve the existing tool-call chips and inline Approve/Cancel controls in the redesigned message row.

## 4. Futuristic restyle & roles

- [x] 4.1 Restyle the panel surface, header, message rows, and composer via the new classes (glass surface, accent glow, elevation) using tokens.
- [x] 4.2 Wire `AssistantAvatar` into assistant message rows / panel header; derive `active` from `chat.streaming` (and running tool calls).
- [x] 4.3 Keep user vs assistant rows visually distinct (alignment / avatar / container).
- [x] 4.4 Confirm autonomy selector, history, new-chat, close, and composer remain present and operable after restyle.

## 5. Full-screen mode

- [x] 5.1 Add `mode: "docked" | "fullscreen"` state to `AgentSidebar` and an expand/collapse control in the header.
- [x] 5.2 Render the same component instance/state in a full-viewport overlay with a centered, width-constrained reading column when `fullscreen`.
- [x] 5.3 Preserve conversation, draft, and scroll position across mode toggles (no unmount); keep an in-flight stream running through the switch.
- [x] 5.4 Collapse on the control and on Escape; ensure the docked panel is restored intact.

## 6. Tests & verification

- [x] 6.1 Update `web/src/components/__tests__/AgentSidebar.test.tsx` selectors for the new markup; keep existing behavior assertions green.
- [x] 6.2 Add tests: markdown renders formatted (heading/list/code) and raw HTML stays inert; streaming tail stays plain.
- [x] 6.3 Add tests: full-screen toggle preserves messages + draft; Escape collapses.
- [x] 6.4 Add a reduced-motion assertion for the avatar (static under `reduce`).
- [x] 6.5 Run `npm run test` and typecheck/build in `web/`; verify the panel in the browser preview (docked + full-screen, streaming, reduced-motion).
- [x] 6.6 Run `graphify update .` after implementation to refresh the graph.
