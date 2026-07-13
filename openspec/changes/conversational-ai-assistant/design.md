## Context

The existing `MigrationAgent` (`src/agent/agent.py`) has three distinct capabilities: plan, guide, and heal. It is called programmatically from services (`onboarding.py`, `lrmis_onboarding.py`, `worker.py`) and from `scripts/agent.py`. Those entry points produce structured Python objects, not a human-facing conversation.

The admin web UI (`web/`) is a React SPA with a sidebar nav, topbar, and main content area. The FastAPI backend (`src/admin_api/`) exposes REST endpoints for reads, actions, job orchestration, and SSE event streams. There is no chat endpoint, no natural-language endpoint, and no sidebar chat component.

The design must:

- Keep all existing agent methods (`plan`, `guide`, `heal`) unchanged; the conversation layer wraps them.
- Reuse the existing multi-provider LLM failover for NL understanding; no new API keys.
- Follow the existing FastAPI router pattern (auth dependency, audit wiring, typed request bodies).
- Follow the existing React component patterns (Tailwind, lucide-react icons, hooks).
- Be free-tier compatible: schema-only prompts, short context windows, timeout/fallback behavior, and offline heuristic templates.

## Goals / Non-Goals

**Goals:**

- Conversational NL interface to existing agent capabilities (status, proposal review, plan, guide, heal).
- MVP with 5-7 high-value intents before expanding to every workflow.
- Sidebar chat UI persistent across admin pages, with page-aware context.
- FastAPI chat endpoint that can stream typed events for progressive response rendering.
- Guarded action mode with propose-only as the default and optional auto-apply-safe for allowlisted tools.
- Workflow guidance for onboarding and deploy in MVP; drift resolution and schema swap can follow.
- Conversation persistence to `integration.agent_conversation` for audit and context resumption.
- All NL understanding uses the existing provider-agnostic LLM failover.
- Heuristic/offline mode works with template-based responses when no LLM is available.

**Non-Goals:**

- NOT replacing the existing dashboard pages; the agent complements them.
- NOT building a general-purpose chatbot.
- NOT adding voice or multimodal input.
- NOT sending row-level PII to any LLM.
- NOT building a standalone mobile app.
- NOT rewriting the existing agent methods.
- NOT adding real-time collaboration.
- NOT shipping broad `auto_all` execution in the MVP.

## Feasibility Assessment

The assistant is worth integrating because the app already has the hard migration intelligence but exposes it as scattered dashboard actions and structured objects. A conversational layer can reduce operator friction most where the task is diagnostic: "why is this blocked?", "what should I review next?", "what changed in the schema?", and "what would happen if I deploy this?".

The easy path is a thin, typed layer over existing services. The risky path is letting an LLM drive workflows directly. The design therefore uses intent classification, typed tools, validated parameters, existing service calls, and explicit confirmation for mutations.

Free-tier AI is viable only if the LLM is treated as optional. The system must run in three modes:

1. **Offline**: heuristic intent detection and templates; no key required.
2. **Free tier**: schema-only LLM calls for better intent/summary quality; best-effort due to quota and latency.
3. **Paid/private provider**: same provider interface, stricter data terms and higher quotas for production.

## Decisions

### Decision 1: Intent-based routing, not free-form chat

The agent detects the user's intent from their message and routes to a typed tool, rather than letting an LLM freely call functions.

```text
User: "What's blocking deploy for entity schools?"
-> Intent: check_deploy_status
-> Tool: deploy_guidance(entity="schools")
-> Response: formatted status + next steps
```

**Rationale**: Free-form function calling on free-tier LLMs is unreliable for operational software. Intent classification is simpler and safer: the NL call classifies the message into a known intent, then the system executes the mapped tool with validated parameters. If confidence is low, the agent asks for clarification.

### Decision 2: MVP intent set

The first release supports only:

- `check_status`
- `summarize_proposal`
- `explain_blocker`
- `show_schema`
- `deploy_guidance`
- `explain_dilemma`
- `onboard_table`

`heal_error`, drift resolution, schema swap, and arbitrary job triggering are later additions unless implementation proves cheap during the MVP.

**Rationale**: These intents answer the highest-value operator questions while minimizing mutation risk and implementation surface.

### Decision 3: Tool registry with typed signatures

Tools are defined as dataclasses in `src/agent/tools.py`:

```python
@dataclass
class ToolDef:
    name: str
    description: str
    params_schema: dict
    handler: Callable
    autonomy: str          # "propose_only" | "auto_safe" | "destructive"
```

The conversation loop loads the tool registry, passes available tools to the NL classifier, calls the matched handler, and formats the result for the user.

**Rationale**: Typed tool definitions make the system testable, auditable, and safe. The autonomy level on each tool prevents the agent from calling destructive tools without confirmation regardless of what the NL layer returns.

### Decision 4: SSE streaming with simple resume semantics

The chat endpoint returns an SSE stream of typed events:

- `conversation` - new or existing conversation metadata.
- `token` - response text chunk for progressive rendering.
- `tool_call` - when the agent is preparing or calling a tool.
- `tool_result` - structured result, possibly including a confirmation prompt.
- `error` - error message.
- `done` - final complete response.

For MVP, reconnect resumes from persisted conversation state and may restart the current response. Exact token replay with `last-event-id` is optional after the assistant is useful.

**Rationale**: The existing job SSE pattern gives the backend and frontend a familiar shape. Exact token replay adds storage and ordering complexity that does not decide whether the assistant is useful.

### Decision 5: Page-aware context injection

The frontend sends the current page route and relevant entity IDs with each chat message:

```json
{
  "page": "/mappings/42",
  "page_title": "Mapping Review",
  "context": {"proposal_id": 42, "entity": "schools"}
}
```

The agent uses this context to disambiguate queries like "deploy this" without the user repeating the entity.

**Rationale**: Page context is explicit, small, and easy to audit. The backend still validates access and never trusts the page context for authorization.

### Decision 6: Conversation persistence in existing DB

A new `integration.agent_conversation` table holds conversations:

```sql
CREATE TABLE integration.agent_conversation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id INTEGER NOT NULL REFERENCES integration.admin_user(id),
    title TEXT NOT NULL DEFAULT '',
    autonomy_tier TEXT NOT NULL DEFAULT 'propose_only',
    messages JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Messages store `{role, content, tool_calls?, tool_results?, created_at}`. Row values never reach this table.

**Rationale**: Existing Postgres avoids a new dependency. JSONB is flexible enough for chat messages and audit review. The table is small because the feature is admin-only.

### Decision 7: Workflow guidance as state machines

Each workflow is a state machine defined in `src/agent/workflows.py`. MVP implements onboarding and deploy guidance first.

```python
WORKFLOWS = {
    "onboard": {
        "steps": ["discover", "propose", "review", "deploy", "backfill"],
        "transitions": {...},
        "entry_tool": "onboard_table",
    }
}
```

**Rationale**: State machines are testable and safe. The LLM explains and classifies; it does not decide the allowed transition graph.

### Decision 8: Autonomy tiers stay conservative

The MVP supports:

| Tier | Behavior |
|---|---|
| `propose_only` | Agent explains what it would do; user approves before mutation. |
| `auto_safe` | Agent may auto-apply allowlisted, high-confidence, non-destructive operations. |

Destructive tools (deploy, schema-swap apply, entity toggle, destructive jobs) always require confirmation. `auto_all` is deferred until there is enough audit data to justify it, and no tool defaults into broad automatic execution.

**Rationale**: The existing system is handling schema changes, data delivery, and operational jobs. Conservative autonomy is a feature, not a limitation.

### Decision 9: Free-tier guardrails

Every LLM call must have:

- Schema-only input; no row values.
- Prompt and output token caps.
- Provider timeout and fallback to the next provider or heuristic mode.
- A deterministic template response for each MVP intent.
- A test that proves row values are excluded from prompts and persisted messages.

**Rationale**: Free-tier APIs are useful for demos and low-volume admin flows, but quotas, latency, and provider data terms are not stable enough to be the only operating mode.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| Free-tier LLM misclassifies intent | Confidence threshold, typed parameter validation, low-confidence clarification, heuristic fallback. |
| Free-tier quota or latency blocks usage | Timeout, provider failover, cached service results, and offline templates. |
| User expects ChatGPT-level fluency | Initial assistant copy frames the feature as a migration assistant, not a general chatbot. |
| Conversation history grows unbounded | Cap messages per conversation; summarize older entries schema-only; enforce configurable `MAX_MESSAGES`. |
| Page context leaks sensitive data | Frontend sends only route and IDs; backend validates access; tests reject row values in prompts and stored messages. |
| Auto-safe performs an unwanted mutation | Only allowlisted tools, confidence threshold, audit log, and destructive tools always require confirmation. |
| SSE token replay adds too much complexity | MVP persists final messages and can restart an in-flight response on reconnect; exact replay is future work. |
| DB migration conflicts with existing schema | New table only, no column changes to existing tables. Migration is additive and reversible. |

## Migration Plan

1. **Land the DB migration**: create `integration.agent_conversation` table with `autonomy_tier`.
2. **Build MVP tool registry** (`src/agent/tools.py`): implement the seven MVP tools first.
3. **Build the conversation loop** (`src/agent/conversation.py`): intent classifier, tool dispatcher, response formatter, and heuristic templates.
4. **Add FastAPI endpoints**: `agent_router`, chat SSE endpoint, and conversation CRUD.
5. **Build the React sidebar chat**: `AgentSidebar.tsx`, `ChatMessage.tsx`, `useAgentChat.ts`.
6. **Wire page-aware context**: pass current route and entity/proposal IDs from Shell/page components.
7. **Implement MVP workflow guidance**: onboarding and deploy state machines.
8. **Add conservative autonomy support**: propose-only default and auto-safe gating.
9. **Add free-tier/privacy tests**: token caps, provider fallback, and no-row-value prompt assertions.
10. **Integration tests**: conversation loop with mocked LLM, tool registry tests, SSE endpoint tests, and UI streaming tests.

Rollback: Remove the `agent_router` from `app.py` includes, delete the `AgentSidebar` import from `App.tsx`, and drop the `integration.agent_conversation` table. All existing functionality is untouched.

## Open Questions

- Should Phase 1 use SSE immediately or start with a normal JSON response and add streaming once the endpoint is stable? Proposal: use SSE because the app already has job SSE patterns, but keep resume semantics simple.
- Should `heal_error` be in MVP? Proposal: include only as propose-only if implementation is cheap; otherwise defer.
- What is the exact first intent list? Proposal: `check_status`, `summarize_proposal`, `explain_blocker`, `show_schema`, `deploy_guidance`, `explain_dilemma`, `onboard_table`.
- Should free-tier provider use be enabled in production by default? Proposal: no. Production should require an explicit provider choice and privacy acknowledgement.
