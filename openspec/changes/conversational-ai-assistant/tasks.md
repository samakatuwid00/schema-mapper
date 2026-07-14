## 0. MVP guardrails and feasibility

- [x] 0.1 (2026-07-14) Confirmed — the seven MVP intents are implemented exactly as listed in `src/agent/tools.py`.
- [x] 0.2 (2026-07-14) Budgets defined in `conversation.py`, env-overridable: `AGENT_MAX_PROMPT_CHARS` (6000, hard cap enforced in `_build_classify_prompt`), `AGENT_MAX_RESPONSE_TOKENS` (512, passed to the provider), `AGENT_LLM_TIMEOUT_SECONDS` (20, provider http timeout), fallback order = the existing `LLM_PROVIDER_ORDER` contract with `heuristic` terminal.
- [x] 0.3 `test_privacy_row_values_never_reach_prompt_or_persistence` — plants row values in BOTH page context and a tool result; proves neither reaches the classification prompt nor any persisted message (three planted markers, prompt capture + storage blob assertions), while schema-level context (proposal_id) survives.
- [x] 0.4 Confirmed in the registry: dispatch tiers are `propose_only`/`auto_safe` only; `destructive` tools (e.g. `recover_from_backup`) always require confirmation regardless of tier, and the wrapped services enforce typed confirmation themselves. No `auto_all` anywhere.

## 1. Database migration

- [x] 1.1 `sql/013_agent_conversation.sql` created (005 was stale — taken) with the D6 columns; applied to the live dev central DB.
- [x] 1.2 `agent_conversation_user_updated_idx (user_id, updated_at DESC)` — matches the only listing query.
- [x] 1.3 `CHECK (autonomy_tier IN ('propose_only','auto_safe'))` — `auto_all` is rejected at the DB in addition to API/service validation.

## 2. MVP tool registry (`src/agent/tools.py`)

- [x] 2.1 (2026-07-14) `ToolDef` lives in `src/agent/tool_defs.py` (created ahead of time by source-schema-swap-and-disaster-recovery §6, per its design) and now carries all six fields — `destructive: bool` added, auto-True when `autonomy == "destructive"`. `tools.py` imports it rather than defining a second dataclass.
- [x] 2.2 `check_status` → `ops_service.get_status()`; auto_safe.
- [x] 2.3 `summarize_proposal` → `get_review()`; counts by field status, low-confidence/unmapped list, unmet required columns, risk high/low. Field rows pass through an explicit allowlist projection (`_FIELD_KEYS`) — reasoning/sample values never reach the output (redaction by construction).
- [x] 2.4 `explain_blocker` → proposal status + accepted-mapping presence + the proposal's persisted `unmet_required_columns` (the stored coverage verdict) → blockers list + `deploy_ready`. Deliberately does NOT re-run `coverage_report` (that's deploy-time's job; the persisted verdict avoids guessing mapping-dict key shapes).
- [x] 2.5 `show_schema` → `ops_service.get_schema_trees()` (optional `source_schema` param passed through).
- [x] 2.6 `deploy_guidance` → composes `explain_blocker` + recommended next actions per blocker; result carries `executed: False` always — never deploys.
- [x] 2.7 `explain_dilemma` → constructs `Dilemma`, calls `MigrationAgent.guide()` (pure), returns options + recommendation.
- [x] 2.8 `onboard_table` → `onboarding.propose()` (creates a needs_review proposal — the propose-only mutation); autonomy `propose_only`; result note states nothing was approved/deployed.
- [x] 2.9 `tests/test_agent_tools.py` — 18 tests: registry membership (9 tools: 7 MVP + the 2 recovery tools registered per the archived change's 6.3), autonomy/destructive flags, param schema validation (required + types), dispatch through monkeypatched service seams for every wrapper, redaction allowlist (a planted "SECRET ROW VALUE" sample never appears in output), real-`guide()` dilemma path, propose-only note, error propagation. Full suite 350 passed.

## 3. Conversation loop (`src/agent/conversation.py`)

- [x] 3.1 `ConversationContext` dataclass with the five specced fields (the session flow passes the pieces explicitly; the dataclass is the documented context shape).
- [x] 3.2 `classify()`/`heuristic_classify()` → `Intent(name, params, confidence, source)`; params extracted from message regexes (proposal #N, onboard <table>, dilemma kinds) merged with page context; confidence floor 0.5 → clarification listing supported actions.
- [x] 3.3 Follows the existing failover contract (`LLM_PROVIDER_ORDER`, skip-unconfigured, terminal `heuristic`): gemini classification uses its own structured-output schema (the mapping providers are hard-wired to the mapping schema so they can't be reused verbatim; openai-compatible names are SKIPPED for classification — documented in code — and the heuristic answers). Timeout + token caps per 0.2; ANY provider error falls through gracefully.
- [x] 3.4 Deterministic template per MVP intent (`TEMPLATES`), + clarification + deferred (swap/drift/recovery → dashboard/CLI pointer) + error texts; all garbage-tolerant.
- [x] 3.5 `ToolDispatcher.dispatch` — schema validation (descriptive error, handler NOT called), autonomy gate (§7), handler call, result redaction, audit callback with tier + auto_executed. Carries `destructive` on the outcome (BUG found by test: the session originally re-resolved the tool from the GLOBAL registry, breaking injected registries).
- [x] 3.6 `ConversationManager` — CRUD scoped by user (foreign access = NotFoundError → 404), title auto-generation (first message, 120 cap), `_cap_messages` context window (> `AGENT_MAX_MESSAGES` collapses older messages into ONE schema-only summary entry: tool names + count, never content), per-user retention prune (default 100) on create.
- [x] 3.7 `AgentSession.process_message` (sync, fully testable) + `converse()` async generator (blocking work via `asyncio.to_thread`); events: conversation → tool_call → tool_result/error → token chunks → done; confirmation round-trip via `confirm={tool, params}` dispatched with confirmed=True.
- [x] 3.8 `tests/test_agent_conversation.py` — 53 tests total in the file: classification (heuristic per intent, LLM via fake gemini client, failover-on-quota-error, unregistered-intent rejection, heuristic-only never calls provider), prompt budget, dispatch tiers, templates, context cap, manager SQL behavior over a fake central, session flows, privacy fixture.

## 4. FastAPI chat endpoints

- [x] 4.1 `agent_router` lives in NEW `src/admin_api/agent_chat.py` (routers.py untouched — same one-file-per-area pattern the recovery router set), prefix `/api/agent`.
- [x] 4.2 `POST /api/agent/chat` — body `{message, conversation_id?, context?, autonomy_tier?, confirm?}`; `EventSourceResponse` of the six typed events; the loop runs in a worker thread so the event loop never blocks; service errors become an `error` event + `done` (stream stays well-formed).
- [x] 4.3 Reconnect = reload persisted conversation by id (`GET /conversations/{id}`); no token replay (tested: after a completed stream, the reload returns the persisted assistant message).
- [x] 4.4/4.5/4.6 CRUD implemented, all scoped by `user_id` in SQL — another user's conversation is a 404 (existence not leaked); DELETE returns `{"ok": true}`.
- [x] 4.7 Every EXECUTED tool audited via `write_audit(actor, "agent:<tool>", target_type="agent_conversation", target_id=<conversation>, details={autonomy, auto_executed, params})`; confirmation PROMPTS are not executions and are not audited (tested both ways).
- [x] 4.8 Included in `create_app()`.
- [x] 4.9 `tests/test_agent_chat_api.py` — 13 integration tests over the REAL routes + REAL loop + REAL registry in heuristic mode: full SSE flow (typed event order, tool result content, persistence), resume, page-context disambiguation, confirmation round-trip through the API, clarification, deferred swap, audit details, auto_all → 422, tier persistence, CRUD + cross-user 404s, stream recovery reload, error event on unknown conversation, anon 401s.

## 5. React sidebar chat UI

- [x] 5.1 `useAgentChat.ts` — fetch-based SSE (EventSource cannot POST; `client.streamAgentChat` parses the stream), message history, streaming/draft state, pendingConfirm, tier, loadConversation/reset. Page context is caller-supplied per message.
- [x] 5.2 `ChatMessage.tsx` — role icon, pre-wrap text with streaming caret, compact tool chips (running/done/failed/needs-approval), inline Approve/Cancel.
- [x] 5.3 `AgentSidebar.tsx` — fixed right panel (closed by default; open-state lives in Shell so it survives navigation), header actions (new chat / history / close), history list with per-item delete (window.confirm), message list, input + send.
- [x] 5.4 Tier selector with EXACTLY `propose_only` and `auto_safe` (test pins the option list — no auto_all).
- [x] 5.5 `pageContextFor(pathname)` derives `{page, context:{proposal_id}}` from the route (e.g. `/mappings/42`); sent with every message and confirmation. NOTE: implemented by route parsing in the sidebar rather than threading props through every page — same data, one seam, exported + unit-tested.
- [x] 5.6 Reconnect: `loadConversation(id)` reloads persisted messages (used by the history list; MVP semantics per D4).
- [x] 5.7 Inline Approve/Cancel rendered on the last message's gated tool_call; Approve re-sends with `confirm={tool, params}`.
- [x] 5.8 Existing class conventions (`btn`/`input`/`panel-header`/`dim`/`mono`) + lucide icons (MessageSquare/Bot/Cog/…); layout via minimal inline styles matching the app's dark shell. (The app is NOT Tailwind — the task's wording was aspirational; matched the actual styling system instead.)
- [x] 5.9 `AgentSidebar` mounted in Shell with a topbar "Assistant" toggle button (the closed-state indicator).
- [x] 5.10 12 new component tests (`ChatMessage` 4: roles, streaming chip, approve/cancel callbacks, no-controls-without-handler; `AgentSidebar` 8: closed-renders-nothing, send-with-route-context, pageContextFor unit, two-tier pin, confirmation forwarding, history switch + delete-with-confirm, new chat, error surface). GATES: `tsc --noEmit` clean, vitest 86 passed (was 74), `npm run build` ok. (jsdom quirk fixed: guarded `Element.scrollTo`.)

## 6. MVP workflow guidance (`src/agent/workflows.py`)

- [x] 6.1 `WorkflowState` dataclass + to_dict/from_dict (persisted on assistant messages).
- [x] 6.2 Onboarding machine: discover → propose → review → deploy → backfill; only the current step may complete (the LLM never decides transitions, D7).
- [x] 6.3 Deploy machine: check_coverage → resolve_dilemmas → confirm → deploy.
- [x] 6.4 Wired: a successful `onboard_table` records onboard-state (discover+propose done → "Next step: review…" appended to the reply); `deploy_guidance` records deploy-state (skipping resolve_dilemmas when ready); "where are we?" (`workflow_status` heuristic intent) answers from the last persisted state with current/done/remaining — no tool call.
- [x] 6.5 `tests/test_agent_workflows.py` — 8 tests (transitions to completion, invalid move rejected + state unchanged, describe, suggestions, dict round-trip, unknown workflow) + 4 wiring tests in the conversation suite.

## 7. Conservative autonomy gating

- [x] 7.1 Gate lives in `ToolDispatcher.dispatch` (single choke point); unsupported tiers (incl. auto_all) raise ValidationError before anything else.
- [x] 7.2 `propose_only`: tools not on the auto_safe allowlist → confirmation prompt, handler untouched (reads marked auto_safe still answer — they're the product).
- [x] 7.3 `auto_safe`: executes only `auto_safe`-marked tools with validated params AND classification confidence ≥ `AGENT_AUTO_SAFE_CONFIDENCE` (0.7); low confidence or non-allowlisted → confirmation prompt.
- [x] 7.4 `destructive` → confirmation in every tier, always; the wrapped services additionally enforce their own typed confirmations (defense in depth — chat approval alone cannot run a restore).
- [x] 7.5 Audit callback records `{autonomy: <tier>, tool_autonomy, auto_executed, error}`; API layer adds actor/conversation (§4.7).
- [x] 7.6 Tier behaviors, low-confidence deferral, destructive guard (auto_safe tier, confidence 1.0 — still deferred), confirmed execution, invalid-params-no-call, auto_all rejection, and mid-conversation tier switch (`test_session_resumes_and_switches_tier` + API `test_tier_change_persists_on_conversation`).

## 8. Later phases

- [ ] 8.1 Add `heal_error` as propose-only unless safe auto-heal is explicitly allowlisted
- [ ] 8.2 Add drift resolution workflow: list drift reports -> diff -> re-map -> apply
- [ ] 8.3 Add schema-swap workflow: dry-run diff -> re-map -> confirm -> recreate -> re-deliver
- [ ] 8.4 Consider exact SSE token replay with `last-event-id` after MVP usefulness is proven
- [ ] 8.5 Revisit `auto_all` only after audit data proves `auto_safe` is reliable

## 9. Integration and regression

- [x] 9.1 `MigrationAgent.converse(message, user_id=..., session=...)` — thin wrapper over `AgentSession.process_message`, returns the final assistant content + conversation id + events; audited via the agent's existing `_record` sink.
- [x] 9.2 `plan`/`guide`/`heal` untouched (converse appended only); full suite green including all pre-existing agent tests.
- [x] 9.3 End-to-end covered at the API seam (`test_full_chat_flow_streams_typed_events`: real chat → real heuristic classify → real registry dispatch → SSE events → persisted messages) + UI render covered by the component tests over the same event shapes. A live browser click-through remains blocked on admin credentials (same limitation noted for the Recovery UI).
- [x] 9.4 The entire API test file runs under `LLM_PROVIDER_ORDER=heuristic`; templates answer every intent; `test_classify_heuristic_only_never_calls_provider` proves no provider call.
- [x] 9.5 Full `pytest -q` → **419 passed** (was 350: +53 conversation, +8 workflows, +13 chat API, -? none removed). sql/013 applied to the live dev central DB.
- [x] 9.6 `tsc --noEmit` clean, vitest 86 passed, `npm run build` ok; no Python linter is configured in this repo (pytest is the gate).
- [x] 9.7 `openspec validate conversational-ai-assistant --strict` → VALID.
