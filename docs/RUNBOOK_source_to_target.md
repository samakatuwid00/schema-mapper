# Runbook — migrate to direct source → target + nightly rebuild

Goal: move entities off the legacy `lrmis_staging` single-table path onto the direct
LRMIS-target path (Path B), then run the one-command nightly rebuild, then cut over.

Current state (measured 2026-07-11 on the dev stack): **124 entities, all on legacy
staging, 0 on the target path** (53 deployed). Target = `lrmis_target` on the 3307
MySQL container; its schema comes from the DDL at
`C:\Users\deped\Documents\lrmis-main\lrmis_db\lrmis.sql`.

Legend: ✅ safe/read-only · ⚠️ writes to the DB · ❗ destructive.

---

## Phase 0 — one-time prerequisites

### 0.1 ✅ CONFIRMED — the LRMIS target *contract* already has the real 51 tables
`propose` maps source columns to whatever tables are in the approved **contract**
schema. Checked 2026-07-11: `discover` returns real LRMIS target candidates
(`author`, `title`, `lr_nonprint`, `beis`, `acquisition`, `datatable_print`, …), so the
contract is the real schema and `propose` will emit multi-table Path B mappings. No
action needed. Re-verify any time with:

```bash
python -m src.pipeline discover --source-schema irimsv --target-system LRMIS
```
(If it ever shows only `irimsv_*_staging` names, the contract regressed — scan
`lrmis_target` and re-approve it as the contract before proposing.)

### 0.2 ⚠️ Fix the source dump encoding
`lrmis_dump.sql` is currently **UTF-16** (made with PowerShell `>`), which `psql`
cannot read. Regenerate it as UTF-8 by letting `pg_dump` write the file directly:

```bash
# from the machine/container that holds the source; example against real_source (5440):
pg_dump -h localhost -p 5440 -U postgres -d real_source -f lrmis_dump.sql
```
Confirm the first line reads `-- PostgreSQL database dump` in plain ASCII (not spaced-out characters).

### 0.3 ✅ Set the nightly restore command
The nightly job refuses to guess a destructive restore command. Point it at your dump +
central DSN (add to `.env` or export):

```bash
LRMIS_SOURCE_DUMP_PATH=./lrmis_dump.sql
# either a full command ({dump} is substituted) …
LRMIS_SOURCE_RESTORE_CMD=psql "postgresql://postgres:postgres@localhost:5433/central" -v ON_ERROR_STOP=1 -f "{dump}"
# … or just set CENTRAL_DSN and let the default psql restore run:
CENTRAL_DSN=postgresql://postgres:postgres@localhost:5433/central
```
> Note: the restore must land the source rows in the `irimsv` schema the pipeline reads.
> Verify your dump targets schema `irimsv` (or adjust the restore command accordingly).

---

## Phase 1 — pilot: migrate ONE entity to the target

Pick one deployed entity (see `python -m src.pipeline status`). Using `<T>` as its table:

```bash
# 1. ✅ draft AI mappings against the LRMIS target schema
python -m src.pipeline propose --source-schema irimsv --source-table <T> --target-system LRMIS
#    -> prints a PROPOSAL_ID

# 2. ✅ review what the AI proposed (per-column target_table.target_column + confidence)
python -m src.pipeline review --proposal <PROPOSAL_ID>

# 3. ⚠️ resolve any low-confidence / unmapped required columns (repeat per column)
python -m src.pipeline resolve --proposal <PROPOSAL_ID> \
    --source-column <src_col> --target-column <lrmis_col> --transform none --resolved-by admin

# 4. ⚠️ deploy to the TARGET path (NOT `pipeline deploy`, which is the legacy staging deploy).
#    deploy_to_lrmis has no CLI yet, so call it directly:
python -c "from src.services.lrmis_onboarding import deploy_to_lrmis; import json; print(json.dumps(deploy_to_lrmis(<PROPOSAL_ID>, 'admin'), default=str))"
#    -> validates the multi-table mapping, sets lrmis_target_tables (flips the entity to Path B), marks deployed
```

Verify the pilot delivers directly to the target:
```bash
# ⚠️ enqueue this entity's current source rows, then run one delivery pass
python -m src.pipeline backfill --entity <T>
python -m src.worker            # one pass; delivers Path B events into lrmis_target

# ✅ confirm it landed on the target path (should now show under "on LRMIS target")
python scripts/lrmis_cutover.py
```
If `lrmis_cutover.py` shows `<T>` under **Path B** and target rows exist, the direct
source→target path is proven on your real data.

---

## Phase 2 — scale to the remaining deployed entities

Repeat Phase 1 steps 1–4 for each deployed entity. For the ~53 deployed entities this is
the bulk of the effort (each needs its AI proposal reviewed/resolved). Two ways to make it
lighter — ask me to add either:
- a `deploy-lrmis --proposal N --by admin` CLI subcommand (removes the `python -c`), and
- a `bulk_deploy_lrmis` that auto-deploys entities whose mappings are all high-confidence and routes the rest to a review list (mirrors the existing `onboard_bulk`).

---

## Phase 3 — nightly rebuild (once entities are on the target)

```bash
# ✅ dry run: shows entities, source counts, reset plan, backup path — changes nothing
python scripts/nightly_refresh.py --dry-run

# ❗ real rebuild WITHOUT source restore (reset target + re-deliver from current source)
python scripts/nightly_refresh.py --confirm lrmis_target

# ❗ full nightly run WITH source restore (needs Phase 0.3)
python scripts/nightly_refresh.py --confirm lrmis_target --restore
```
It truncates the target to a fresh, lookup-seeded state and re-delivers every current
source row. Schedule the last command at midnight via Windows Task Scheduler (or run the
`nightly_refresh` admin job).

---

## Phase 4 — cutover (only after ALL deployed entities are on the target)

This is the destructive removal. Do NOT start until `lrmis_cutover.py` shows every deployed
entity under Path B and none on legacy staging.

1. Have me implement §2 (collapse `worker.py` to the single direct path) and §5 (delete the
   staging code paths, cleanup jobs, and drop `lrmis_staging` / `lrmis_staging_views`).
2. Run the full `pytest` suite, then `graphify update .`.
3. Remove the staging browser/pages in the UI and unify on "delivery" language (§6).

See `openspec/changes/simplify-source-to-target-delivery/tasks.md` for the task list.
