## Why

The current `MigrationAgent` (`src/agent/agent.py`) is a backend class that returns structured data objects: plans, risks, guidance options, and heal proposals. There is no interactive interface for a database administrator to ask "what is blocking deployment for entity X?", get step-by-step guidance through onboarding, mapping review, deploy, and error resolution, or prepare safe agent actions for approval. Without this, the AI agentic capability described in the generic AI database migration engine remains a backend feature instead of an operator-facing assistant.

## Feasibility Verdict

This is helpful if the assistant is kept task-oriented: explain migration status, identify blocking mappings, summarize proposal risks, guide the next workflow step, and prepare safe actions for approval. It is not helpful as a general chatbot bolted onto the dashboard; that would add latency, cost, and trust risk without reducing operator work.

It is moderately easy as an MVP because the hard domain logic already exists in `MigrationAgent`, `ops_service`, onboarding services, job orchestration, audit logging, and existing SSE infrastructure. It becomes difficult if the first release includes full conversation replay, broad autonomous execution, every workflow, and polished ChatGPT-style behavior. The first useful release should be a narrow guided assistant with 5-7 intents, page-aware context, propose-only execution, and heuristic/offline templates.

It is possible on free-tier AI APIs for development and low-volume admin usage because prompts are schema-only, calls are short, and the existing heuristic provider keeps the assistant usable with no API key. Free tiers should be treated as best-effort, not a production guarantee: rate limits change, latency can spike, and some providers use free-tier traffic for product improvement. Production deployments should keep the provider abstraction and allow a paid or private provider without changing the assistant design.

## What Changes

- **MVP-first assistant**: Phase 1 focuses on the most useful operator questions: status, blocking deploy reasons, proposal review summaries, schema/drift explanation, and guided next steps. Broader workflow automation lands after the MVP proves useful.
- **Conversational AI core** (`src/agent/conversation.py`): Chat loop with message history, intent detection, and tool-calling that maps natural-language queries to agent operations (plan, guide, heal, status, deploy guidance). Uses the existing multi-provider LLM layer (`propose_mapping` failover) for NL understanding; no new API key is required.
- **Chat API endpoint** in the admin API (`POST /api/agent/chat`): Accepts a user message and conversation ID, returns the agent's response. Stateless per-request, with context loaded from DB. Supports SSE streaming for progressive rendering.
- **Sidebar assistant UI** in the React admin frontend: A collapsible chat panel that stays visible across all pages (Overview, Tables, Review Queue, Schema Changes, etc.), with page-aware context so the agent knows what the user is looking at.
- **Guarded action mode**: Configurable autonomy starts with propose-only and optional auto-apply-safe. Safe operations (heuristic name matches above 0.9, safe casts) can auto-apply only when the tool is explicitly allowlisted; destructive actions always require human confirmation. Broad auto-apply-all is out of MVP scope.
- **Workflow guidance**: Pre-built conversational flows for onboarding (discover -> propose -> review -> deploy -> backfill), drift resolution, and schema swap. The agent leads the user through each step instead of requiring them to hunt through menu items.
- **Wiring into existing services**: Chat agent can call `propose_mapping`, `deploy_guidance`, `coverage_report`, `resolve`, `backfill`, etc. through the existing service layer; no bypass of business logic.
- **Conversation persistence**: Chat history saved to `integration.agent_conversation` table for audit, context resumption, and schema-only feedback data.
- **LLM budget and privacy guardrails**: Token caps, provider timeout/fallback, schema-only prompt checks, and redaction of row values are acceptance criteria, not implementation details.
- **BREAKING**: None. All additions are opt-in and coexist with the existing dashboard; the agent adds a new interaction mode without removing existing ones.

## Capabilities

### New Capabilities

- `conversational-agent-core`: Chat loop, intent detection, tool registry, message history, context management, and template fallback.
- `agent-chat-api`: FastAPI endpoint (`POST /api/agent/chat`) with SSE streaming, conversation CRUD, and agent-action audit logging.
- `agent-sidebar-ui`: React sidebar chat component in the admin web UI, page-aware, with streaming response rendering and confirmation dialogs for guarded actions.
- `workflow-guidance`: Pre-built conversational flows for onboarding, deploy, drift resolution, and schema swap. MVP implements onboarding/deploy guidance first.
- `autonomous-actions`: Configurable autonomy tiers (propose-only and auto-apply-safe in MVP; auto-all deferred) with confidence gating, safe-operation allowlist, and human-confirmation guard for destructive actions.
- `conversation-persistence`: Agent conversation history stored in `integration.agent_conversation` for audit trail, context resumption across sessions, and schema-only feedback data.

### Modified Capabilities

- `migration-management`: The agent becomes a first-class interaction mode alongside the CLI and web UI. `MigrationAgent` gains conversation support, not just programmatic methods.

## Impact

- **New file** `src/agent/conversation.py`: conversational loop, intent router, tool registry integration, and response formatting.
- **New file** `src/agent/tools.py`: typed tool definitions that the chat agent can call (plan, guide, heal, deploy_status, trigger_job, etc.).
- **Modified** `src/admin_api/routers.py`: new `agent_router` with `/api/agent/chat` (POST), `/api/agent/conversations` (GET), `/api/agent/conversations/{id}` (GET/DELETE).
- **New DB migration**: `integration.agent_conversation` table (id, user_id, title, messages JSONB, autonomy_tier, created_at, updated_at).
- **Modified** `web/src/App.tsx`: add `AgentSidebar` component to the Shell layout.
- **New React components** `web/src/components/AgentSidebar.tsx`, `web/src/components/ChatMessage.tsx`, `web/src/hooks/useAgentChat.ts`.
- **Modified** `MigrationAgent` (`src/agent/agent.py`): add `converse()` entry point that routes NL intents to existing methods, optionally with an injected conversation logger.
- **Worker** unaffected; agent heal wiring is unchanged. The conversational agent adds a human-facing explanation layer on top.
- **Dependencies**: No new Python dependencies. React frontend should use existing Tailwind/lucide patterns unless a small markdown renderer is already present.
- **Free-tier compatible**: All LLM calls use the existing multi-provider failover with schema-only prompts, small context windows, request timeouts, and heuristic fallback. Conversation history stores only metadata and agent actions, never row values. Free-tier support is best-effort; the system must degrade cleanly when quota is exhausted.
