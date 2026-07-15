## Context

The assistant already supports diagnostic and repair tools:

- `inspect_job`
- `explain_deploy_error`
- `plan_refresh_failure_repair`
- `add_mapping`
- `reject_mapping`
- `reject_mapping_review`
- `reopen_mapping_review`

The current gap is not raw capability; it is operator flow. A failed deploy message such as:

```text
#ff2c6da7-30e7-4711-b159-282e96c23704 deploy_lrmis
failed
mapping cannot be deployed:
- station_name.id is required but no source column maps to it
```

contains enough information to recover the proposal, parse the missing target columns, and guide a safe repair. The UI should make that path obvious without requiring the operator to know the proposal id.

The requested `//ui-ux-pro-max` direction is treated here as a quality bar: dense, scan-friendly, animated but accessible, operationally useful, and careful with destructive actions. No separate design system or unsafe autonomous behavior is introduced.

## Goals / Non-Goals

**Goals:**

- Let an operator paste a failed deploy job message and get a concrete repair plan.
- Resolve job UUID to `proposal_id` when the job stored one in params.
- Offer confirmation-gated missing mapping additions, especially common `*.id` fan-out repairs.
- Make the exact proposal easy to reopen from chat or the failed job card.
- Add search and bulk delete for chat history.
- Make autonomy mode understandable without exposing only raw enum names.
- Prevent full-screen chat from obscuring job inspection.
- Improve motion and layout polish while honoring `prefers-reduced-motion`.

**Non-Goals:**

- No automatic deployment after a repair without explicit user action.
- No broad `auto_all` autonomy tier.
- No row values sent to the LLM or persisted in chat history.
- No replacement of the Mapping Review page; the assistant routes users back to it.
- No destructive database cleanup through chat history bulk delete beyond deleting conversation rows owned by the current user.

## Decisions

### D1: Job id is the primary recovery handle

When pasted text includes a UUID, the assistant first treats it as a possible `admin_job.id`. If the job exists and `job_type` is `deploy_lrmis` or `deploy`, the assistant reads safe metadata:

- job id
- job type
- status
- params with private/internal keys removed except the server-side `proposal_id`
- error message
- result summary

The assistant uses the job's stored `proposal_id` instead of asking the operator to find it.

### D2: Missing required target mappings become a repair draft, not an automatic mutation

For each parsed missing target column:

- If the target column is `id`, suggest `source.id -> target_table.id` as a high-confidence draft when the source proposal has an `id` source column.
- If the target column is not `id`, list it as a manual mapping requirement unless a deterministic source-column match exists.
- Group all proposed additions into one confirmation prompt so the user does not click through eight identical repairs.

After confirmation, the tool may call existing mapping services to add rows. The proposal remains subject to the normal review/approval/deploy gates.

### D3: Repair answers render action chips

Chat responses for deploy repair should include structured actions the frontend can render:

- Open proposal
- Add missing id mappings
- Reopen affected review rows
- Inspect job
- Retry deploy after approval

The text answer remains useful in plain SSE clients, but the web UI should render buttons/chips for the common actions.

### D4: Chat history search is local-first, API-backed

For a small history list, frontend filtering is acceptable after `GET /conversations`. The API should also accept a search query so larger histories do not require loading every message. Search should match title and safe message text for conversations owned by the user.

Bulk delete is server-side and scoped by conversation ids. The backend ignores or rejects ids the user does not own; it must not leak whether another user's conversation exists.

### D5: Autonomy control is a segmented control with explanations

Replace or augment the raw select with two labeled choices:

- "Ask first" (`propose_only`) - read-only answers run; changes require confirmation.
- "Auto safe" (`auto_safe`) - safe read-only/high-confidence actions may run; destructive actions still require confirmation.

The persisted API value remains unchanged. The UI label becomes friendlier, and a tooltip/help text explains what changes in each mode.

### D6: Full-screen assistant and job drawer coordinate

When the assistant is full-screen, job inspection must remain reachable. Acceptable layouts:

- a split full-screen mode with a job details rail when launched from a job; or
- a pinned job card inside the full-screen assistant; or
- a job drawer that overlays above the assistant with clear z-index and close controls.

The selected implementation must let the operator read a specific failed job and use chat repair at the same time.

### D7: Motion is purposeful and accessible

Slide transitions should communicate spatial changes:

- docked assistant open/close
- full-screen expand/collapse
- history panel reveal
- job detail/repair panel reveal

All motion must be disabled or simplified under `prefers-reduced-motion: reduce`.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| Incorrectly adding `source.id -> target.id` | Only draft the action when the proposal has a source `id`; require confirmation; keep normal approval/deploy validation. |
| Bulk delete removes useful audit context | Confirm with count and search/filter scope; delete only current user's conversations; audits for tool executions remain separate. |
| Search across messages exposes sensitive values | Existing row-value redaction remains required before persistence; search only persisted, redacted content. |
| Autonomy labels hide exact behavior | Show enum value in tooltip or detail text for operators who need precision. |
| Full-screen and job drawer z-index regressions | Add component tests and visual smoke checks for docked, full-screen, and pinned job states. |
| Animation distracts | Keep durations short, use existing tokens, and honor reduced motion. |

## Migration Plan

1. Add backend diagnostics for `deploy_lrmis` job repair context.
2. Add a confirmation-gated bulk missing-mapping repair tool.
3. Add chat routing/templates/action metadata for pasted deploy job errors.
4. Add conversation search and bulk-delete API support.
5. Update `useAgentChat` and API types.
6. Update `AgentSidebar` history UI with search, selection, and bulk actions.
7. Replace the raw autonomy dropdown experience with labeled segmented controls or an enhanced dropdown.
8. Update `JobDrawer` failed-job cards with "Repair with Assistant" and "Open proposal" when recoverable.
9. Coordinate full-screen assistant with pinned job context.
10. Add slide transitions and reduced-motion fallbacks.
11. Add focused backend/frontend tests and run OpenSpec validation.

Rollback: disable new UI controls and routes while leaving existing single-delete, chat, and job drawer behavior intact.

## Open Questions

- Should bulk conversation delete support "delete all matching search results" immediately, or only selected visible rows for the first release?
- Should the missing id repair tool add all drafted mappings in one transaction or stop on first invalid target?
- Should "Retry deploy" remain a link/button to the existing deploy confirmation modal, or should chat prepare the deploy job confirmation directly?

