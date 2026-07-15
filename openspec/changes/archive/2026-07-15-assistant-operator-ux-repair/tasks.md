## 1. Deploy-error repair capability

- [x] 1.1 Add a read-only diagnostic that resolves a pasted job UUID to a deploy job, safe job metadata, entity/source table, and `proposal_id` when available.
- [x] 1.2 Extend deploy-error parsing to combine job error text plus stored job params so missing required columns are tied to the exact proposal.
- [x] 1.3 Add a confirmation-gated tool that can add a batch of missing mapping rows to one proposal, with validation for source column, target table, and target column.
- [x] 1.4 Draft common `*.id` repairs from `source.id` only when the proposal includes an `id` source column; otherwise require manual mapping.
- [x] 1.5 Render repair results with the exact proposal id, missing columns, added mappings, and a next step to review/approve/redeploy.
- [x] 1.6 Add backend tests for pasted job text, missing proposal id fallback, common id fan-out drafts, and confirmation-gated mutation.

## 2. Job drawer recovery UX

- [x] 2.1 Include recoverable `proposal_id`/entity metadata in failed deploy job read models or derive it client-side from safe job detail.
- [x] 2.2 Add "Open proposal" and "Repair with Assistant" actions to failed `deploy_lrmis` job cards when a proposal can be recovered.
- [x] 2.3 When repair is launched from a job card, open the assistant with pinned job context and prefilled repair prompt or direct context payload.
- [x] 2.4 Ensure full-screen assistant does not obscure the selected job; implement a pinned job card, split rail, or coordinated overlay.
- [x] 2.5 Add component tests for failed job actions and full-screen/job coexistence.

## 3. Chat history search and bulk delete

- [x] 3.1 Add API support for conversation search by title and redacted message text, scoped to the authenticated user.
- [x] 3.2 Add API support for bulk deleting selected conversation ids, scoped to the authenticated user and non-leaking for foreign ids.
- [x] 3.3 Add a search box to the history panel.
- [x] 3.4 Add multi-select controls and bulk delete selected conversations with a count-based confirmation.
- [ ] 3.5 Optionally add "delete all matching filter" only if the confirmation clearly names the filtered count. (skipped — optional, first release ships selected-only bulk delete)
- [x] 3.6 Add API and component tests for search, selection, selected bulk delete, and empty states.

## 4. Autonomy control usability

- [x] 4.1 Replace or enhance the raw autonomy dropdown with labeled choices for `propose_only` and `auto_safe`.
- [x] 4.2 Add short help text/tooltips explaining what each mode can do and that destructive actions still require confirmation.
- [x] 4.3 Persist the same backend enum values and keep `auto_all` unavailable.
- [x] 4.4 Add tests that the labels render, enum values are submitted, and unsupported tiers remain rejected.

## 5. Motion and UI polish

- [x] 5.1 Add slide transitions for docked open/close, full-screen expand/collapse, history reveal, and job repair panel reveal.
- [x] 5.2 Keep transitions short and token-based; do not introduce a second color/theme system.
- [x] 5.3 Add reduced-motion fallbacks that remove or simplify looping and sliding animation.
- [x] 5.4 Improve scan layout for repair answers: grouped missing columns, action chips, proposal/job ids, and clear next steps.
- [x] 5.5 Add component tests or visual smoke checks for reduced motion and repair answer layout.

## 6. Validation

- [x] 6.1 Run focused Python tests for diagnostics, tools, and chat routing.
- [x] 6.2 Run focused frontend tests for AgentSidebar, ChatMessage, JobDrawer, and API client changes.
- [x] 6.3 Run `npm run build` or the repo's frontend validation gate.
- [x] 6.4 Run `openspec validate assistant-operator-ux-repair --strict`.
