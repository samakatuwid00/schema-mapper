## Why

Operators can now ask the assistant for mapping and job guidance, but several daily repair workflows still require manual hunting:

- A failed `deploy_lrmis` job stores the `proposal_id`, but the visible error often shows only a job id and missing target columns. Once the proposal leaves the normal review queue, the operator must manually find or reconstruct the mapping.
- Chat history supports one-at-a-time deletion, but not search, bulk cleanup, or quick triage of old diagnostic sessions.
- The autonomy selector exposes machine labels (`propose_only`, `auto_safe`) without enough explanation at the moment the user chooses a mode.
- The full-screen assistant and the right-side job drawer can compete for screen space, making it hard to inspect a specific job while repairing it through chat.
- The assistant UI has a polished base, but the repair workflow needs stronger motion, affordances, and scan-friendly layout so it feels like an operational cockpit rather than a text drawer.

This change turns the previous recommendation into an operator-facing repair flow: paste or select a failed job, recover the hidden proposal id, parse the missing required mappings, offer confirmation-gated repair actions, and open the exact mapping review.

## What Changes

- **Deploy-error repair from job text**: the assistant can parse a pasted failed job message, extract the job UUID, look up `admin_job.params.proposal_id`, parse missing required target columns, and prepare gated mapping repairs.
- **Mapping review recovery**: when a failed deploy proposal is no longer visible in the review queue, the UI and assistant expose direct links/actions to `/mappings/{proposal_id}`.
- **Bulk chat-history management**: users can search conversation history, select multiple conversations, bulk delete selected conversations, and bulk clear filtered history after confirmation.
- **More usable autonomy control**: replace the raw dropdown experience with labeled choices, plain-language descriptions, tooltips/help, and visible current-mode impact.
- **Job drawer coexistence with assistant full screen**: full-screen chat must not hide job inspection. Users can pin or open a specific job alongside the assistant, or launch a repair chat from a failed job card.
- **Slide/motion polish**: add smooth slide transitions for opening, full-screen expansion, history panel, and job-inspection panels, while honoring reduced-motion settings.
- **Capability improvements**: add explicit assistant commands and rendered action chips for inspect job, repair deploy mapping, open proposal, add missing id mappings, and retry deploy after approval.

## Capabilities

### Modified Capabilities

- `agent-sidebar-ui`: improves chat history management, autonomy controls, full-screen/job drawer layout, animations, and repair action affordances.
- `agent-chat-api`: adds bulk conversation delete and history search endpoints or query options.
- `conversation-persistence`: supports efficient search and bulk deletion scoped to the authenticated user.
- `conversational-agent-core`: enhances deploy-error parsing by resolving pasted job ids to proposal ids and preparing missing mapping repair actions.
- `workflow-guidance`: adds a deploy-failure repair flow that guides the user from failed job to proposal repair to redeploy.
- `job-orchestration`: exposes failed job metadata in a way the UI can link to assistant repair and mapping review recovery.

## Impact

- **Backend**:
  - `src/services/operator_diagnostics.py`
  - `src/agent/conversation.py`
  - `src/agent/tools.py`
  - `src/admin_api/agent_chat.py`
  - `src/admin_api/jobs.py` and/or job read models
- **Frontend**:
  - `web/src/components/AgentSidebar.tsx`
  - `web/src/components/ChatMessage.tsx`
  - `web/src/components/JobDrawer.tsx`
  - `web/src/hooks/useAgentChat.ts`
  - `web/src/api/client.ts`
  - `web/src/api/types.ts`
  - `web/src/styles.css`
- **Tests**:
  - Agent diagnostic/tool tests for job-id-to-proposal repair.
  - API tests for search and bulk delete.
  - Component tests for history search/bulk delete, autonomy UI, job repair launch, and full-screen/job coexistence.
- **No breaking changes**: existing conversations, job rows, mapping proposal routes, and SSE event shapes remain compatible.

