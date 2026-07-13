## 0. MVP guardrails and feasibility

- [ ] 0.1 Confirm MVP intent list: `check_status`, `summarize_proposal`, `explain_blocker`, `show_schema`, `deploy_guidance`, `explain_dilemma`, `onboard_table`
- [ ] 0.2 Define free-tier budget limits: max prompt tokens, max response tokens, provider timeout, retry/fallback order
- [ ] 0.3 Add prompt/privacy test fixture proving row values are excluded from prompts and persisted messages
- [ ] 0.4 Confirm `auto_all` is deferred; MVP supports only `propose_only` and optional `auto_safe`

## 1. Database migration

- [ ] 1.1 Create `sql/005_agent_conversation.sql` with `CREATE TABLE integration.agent_conversation` (id UUID PK, user_id FK, title TEXT, autonomy_tier TEXT DEFAULT 'propose_only', messages JSONB, created_at, updated_at)
- [ ] 1.2 Add index on `user_id` + `updated_at` for conversation listing queries
- [ ] 1.3 Add a CHECK constraint limiting `autonomy_tier` to `propose_only` or `auto_safe`

## 2. MVP tool registry (`src/agent/tools.py`)

- [ ] 2.1 Define `ToolDef` dataclass (name, description, params_schema, handler, autonomy, destructive)
- [ ] 2.2 Implement `check_status` tool; wraps `ops_service.get_status()`
- [ ] 2.3 Implement `summarize_proposal` tool; wraps `get_review()` and summarizes confidence/risk
- [ ] 2.4 Implement `explain_blocker` tool; wraps proposal status, coverage, and deploy readiness checks
- [ ] 2.5 Implement `show_schema` tool; wraps `ops_service.get_schema_trees()`
- [ ] 2.6 Implement `deploy_guidance` tool; wraps existing deploy guidance/readiness services without executing deploy
- [ ] 2.7 Implement `explain_dilemma` tool; wraps `MigrationAgent.guide()`
- [ ] 2.8 Implement `onboard_table` tool; wraps discover/propose flow but defaults to propose-only for mutations
- [ ] 2.9 Write unit tests for every MVP tool: schema validation, handler dispatch, error handling, redaction

## 3. Conversation loop (`src/agent/conversation.py`)

- [ ] 3.1 Implement `ConversationContext` dataclass (conversation_id, messages, page_context, workflow_state, autonomy_tier)
- [ ] 3.2 Implement intent classifier: takes user message + available tool names/descriptions, returns (intent_name, params, confidence)
- [ ] 3.3 Wire intent classifier to use existing LLM provider failover with token caps and timeout fallback
- [ ] 3.4 Implement heuristic/template fallback responses for every MVP intent
- [ ] 3.5 Implement `ToolDispatcher`; validates params against schema, checks autonomy gate, calls handler, formats result
- [ ] 3.6 Implement `ConversationManager`; load/save messages to DB, manage context window, summarize old messages schema-only
- [ ] 3.7 Implement `AgentSession.converse(message, context) -> AsyncGenerator[StreamEvent]`
- [ ] 3.8 Write unit tests for classification (mocked LLM), dispatch, template fallback, context management, and no-row-value storage

## 4. FastAPI chat endpoints

- [ ] 4.1 Add `agent_router = APIRouter(prefix="/api/agent", ...)` in `routers.py`
- [ ] 4.2 Implement `POST /api/agent/chat`; accepts `{"message": str, "conversation_id"?: str, "context"?: dict}`, returns SSE stream with typed events (conversation, token, tool_call, tool_result, error, done)
- [ ] 4.3 Implement simple reconnect behavior: persisted conversation resumes, but exact token replay is not required in MVP
- [ ] 4.4 Implement `GET /api/agent/conversations`; list user's conversations (id, title, created_at, updated_at, message_count, autonomy_tier)
- [ ] 4.5 Implement `GET /api/agent/conversations/{id}`; get full conversation scoped to user
- [ ] 4.6 Implement `DELETE /api/agent/conversations/{id}`; delete conversation scoped to user
- [ ] 4.7 Audit wiring: log agent tool calls to existing audit system
- [ ] 4.8 Include `agent_router` in `app.py` `create_app()`
- [ ] 4.9 Write integration tests: SSE streaming, CRUD, auth scoping, audit entries, quota fallback

## 5. React sidebar chat UI

- [ ] 5.1 Create `web/src/hooks/useAgentChat.ts`; manages SSE connection, message history, streaming state, and page context injection
- [ ] 5.2 Create `web/src/components/ChatMessage.tsx`; renders a single message with markdown-lite text, tool call indicators, and confirmation buttons
- [ ] 5.3 Create `web/src/components/AgentSidebar.tsx`; collapsible right panel with message list, input box, conversation history dropdown, autonomy tier selector, and toggle button
- [ ] 5.4 Add autonomy tier selector UI for `propose_only` and `auto_safe`
- [ ] 5.5 Wire page-aware context: pass current route + entity/proposal IDs from Shell/page components to the chat hook
- [ ] 5.6 Add reconnect behavior that reloads the persisted conversation after a dropped stream
- [ ] 5.7 Add confirmation button rendering inline in chat messages for `tool_call` events requiring approval
- [ ] 5.8 Style with Tailwind to match the existing admin UI; use lucide icons for compact controls
- [ ] 5.9 Add `AgentSidebar` to the `Shell` component in `App.tsx`
- [ ] 5.10 Write component tests: rendering, streaming, confirmation flow, page-context injection

## 6. MVP workflow guidance (`src/agent/workflows.py`)

- [ ] 6.1 Define `WorkflowState` dataclass (workflow_name, current_step, completed_steps, context)
- [ ] 6.2 Implement onboarding workflow state machine: discover -> propose -> review -> deploy -> backfill
- [ ] 6.3 Implement deploy guidance workflow: check coverage -> resolve dilemmas -> confirm -> deploy
- [ ] 6.4 Wire workflow state into `ConversationContext` so the agent suggests next steps
- [ ] 6.5 Write tests for MVP workflows: state transitions, valid/invalid moves, completion detection

## 7. Conservative autonomy gating

- [ ] 7.1 Implement autonomy gate in `ToolDispatcher`; checks tool autonomy against conversation tier before executing
- [ ] 7.2 `propose_only`: all mutating tools return confirmation prompts instead of executing
- [ ] 7.3 `auto_safe`: auto-execute only tools marked `auto_safe` when parameters validate and confidence exceeds threshold
- [ ] 7.4 Destructive tool detection: tools marked `destructive` always require confirmation regardless of tier
- [ ] 7.5 Audit logging: record autonomy tier + whether action was auto-executed or confirmed
- [ ] 7.6 Write tests: each tier behavior, destructive guard, tier switching mid-conversation

## 8. Later phases

- [ ] 8.1 Add `heal_error` as propose-only unless safe auto-heal is explicitly allowlisted
- [ ] 8.2 Add drift resolution workflow: list drift reports -> diff -> re-map -> apply
- [ ] 8.3 Add schema-swap workflow: dry-run diff -> re-map -> confirm -> recreate -> re-deliver
- [ ] 8.4 Consider exact SSE token replay with `last-event-id` after MVP usefulness is proven
- [ ] 8.5 Revisit `auto_all` only after audit data proves `auto_safe` is reliable

## 9. Integration and regression

- [ ] 9.1 Wire `AgentSession.converse()` into `MigrationAgent` as a thin `converse()` wrapper
- [ ] 9.2 Verify existing agent methods (`plan`, `guide`, `heal`) remain unchanged and existing tests pass
- [ ] 9.3 Full end-to-end test: chat -> intent classify -> tool dispatch -> SSE response -> UI render
- [ ] 9.4 Heuristic/offline mode test: `LLM_PROVIDER_ORDER=heuristic` -> template responses work
- [ ] 9.5 Run full test suite: `pytest -q`
- [ ] 9.6 Check lint/typecheck if available
- [ ] 9.7 Run `openspec validate conversational-ai-assistant --strict`
