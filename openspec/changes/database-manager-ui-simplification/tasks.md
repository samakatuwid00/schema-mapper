# Tasks: Simplify the Database Manager Experience

## 1. Data browser backend

- [x] 1.1 Add read-only `fetch_rows` / `count_rows` helpers to `PostgresCentralConnector` and `MySQLStagingConnector`, reusing the existing identifier guard; no generic SQL passthrough
- [x] 1.2 Create `src/services/data_browser.py`: `list_browsable_tables()`, `fetch_rows()` (allowlisted table + sort column, size cap, direction enum), `compare_row()` keyed on `external_reference`
- [x] 1.3 Add `GET /api/data/tables`, `GET /api/data/rows`, `GET /api/data/compare` with `require_operator`, `data_browse` audit rows, and `Cache-Control: no-store`

## 2. Bulk onboard + proposal listing

- [x] 2.1 Add `ops.list_proposals(status=None)` and `GET /api/proposals`
- [x] 2.2 Add `onboarding.onboard_bulk()` composing discover/propose/deploy/backfill; four outcome buckets; continue-on-error; per-table progress callback
- [x] 2.3 Register `onboard_bulk` in `JOB_HANDLERS` and add its `_SCOPED` conflict key
- [x] 2.4 Guard tier in `routers.submit_job`: confirm+reason; typed confirmation when the batch redeploys a deployed table

## 3. Backend tests

- [x] 3.1 Data browser: unknown table rejected, non-existent sort column rejected, page size clamped, `data_browse` audit row written
- [x] 3.2 `onboard_bulk`: continues past a failing table; a sub-threshold or unmet-required proposal lands in `needs_review` and is never deployed; already-deployed table is skipped
- [ ] 3.3 Overlapping `onboard_bulk` submissions produce exactly one job and one 409
- [x] 3.4 `pytest -q` and `python -m compileall -q src scripts tests` clean

## 4. Design system

- [ ] 4.1 Self-host Fira Code + Fira Sans; wire `--mono` / `--sans` and tabular numerals for data cells
- [ ] 4.2 Rework `web/src/styles.css` tokens: deepened surfaces, glass panels, one elevation scale, cyan glow reserved for live/active state, motion tokens with a `prefers-reduced-motion` guard
- [ ] 4.3 Fix the status color language in `StatusChip.tsx`: one meaning per color, text label always present
- [ ] 4.4 Add `lucide-react`; replace unicode glyph icons

## 5. Frontend workflow

- [ ] 5.1 Add `web/src/labels.ts` glossary and apply it across nav and pages
- [ ] 5.2 Regroup nav in `App.tsx` into Monitor / Set up / Maintain
- [ ] 5.3 New `DataBrowser.tsx`: table rail (source/staging + row counts), sortable paginated grid with `∅` for NULL, Compare view
- [ ] 5.4 New `Tables.tsx`: table list with one state + one action each, checkbox multi-select, "Onboard selected", result buckets
- [ ] 5.5 Mapping review: proposal picker list (no typed ids) and inline review drawer
- [ ] 5.6 Merge `SchemaScanner.tsx` + `DriftReports.tsx` into `SchemaChanges.tsx`; collapse raw JSON behind details
- [ ] 5.7 Overview: animated source → queue → staging pipeline diagram + inline SVG sparklines
- [ ] 5.8 Frontend tests: nav groups render, labels map internal → presented, status colors unique, guarded tiers still enforced

## 6. Verification and docs

- [ ] 6.1 `npm run build` and `npm test -- --run` clean
- [ ] 6.2 E2E on the Docker stack: log in, bulk onboard all tables, verify the four buckets, open Data Browser, verify staging rows match source via Compare
- [ ] 6.3 Prove non-destructiveness: after bulk onboard over an already-deployed table, its staging rows survive and no unexpected snapshot/drop occurred
- [ ] 6.4 Accessibility pass: body text ≥ 4.5:1 contrast, visible focus rings, usable under `prefers-reduced-motion: reduce`
- [ ] 6.5 Update README/CLAUDE.md for the new pages and vocabulary; run `graphify update .`
- [ ] 6.6 `openspec validate database-manager-ui-simplification --strict`; archive after acceptance
