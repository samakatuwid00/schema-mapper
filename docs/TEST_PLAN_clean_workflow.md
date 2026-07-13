# Clean-workflow test plan — schema_mapper

Goal: prove, on your real dev stack, the three things we discussed — (1) the AI agent
actually plans/guides/heals, (2) the direct source→target path works and staging is
bypassable for at least one entity, (3) the system tolerates a target schema/dialect
change (the MySQL↔Postgres flexibility). Each phase is safe to stop after; nothing
here touches the 124 live entities on the legacy path until you explicitly choose to.

Legend: ✅ safe/read-only · ⚠️ writes to a DB · ❗ destructive/irreversible.

---

## Phase 0 — Clear the git lock, then commit a safety net

Your `.git/index.lock` is stuck (leftover from a git command run through the Claude
desktop bridge, which can create files on your machine but can't delete them). Run
this yourself in a local terminal (PowerShell or Git Bash) in the repo root:

```powershell
# 0.1 — confirm no real git process is running (Task Manager: no git.exe), then:
del .git\index.lock

# 0.2 — verify the .gitignore edits took effect
git status --short | Select-String -NotMatch graphify

# 0.3 — untrack the files now covered by .gitignore (content stays on disk)
git rm -r --cached graphify-out
git rm --cached web/tsconfig.tsbuildinfo

# 0.4 — commit EVERYTHING currently sitting uncommitted: the generic-engine work,
#        the AI agent (src/agent/), adapters/dialect/delivery packages, drift
#        resolution, three-way observability, staging-cleanup fixes, new tests,
#        and the new openspec change specs. This is ~2 weeks of work with zero
#        git history behind it right now — commit before you start testing below,
#        so a bad experiment is one `git reset` away from safe instead of unrecoverable.
git add -A
git commit -m "Checkpoint: generic engine, AI agent, drift resolution, 3-way observability, staging-cleanup fixes"
```

If `del` also refuses (same lock-file quirk), close VS Code / GitHub Desktop / any
other Git GUI first — one of them likely has the repo open and is holding the handle.

---

## Phase 1 — Environment sanity (✅ mostly read-only)

```bash
docker compose up -d
pip install -r requirements.txt
python -m compileall -q src scripts tests
pytest -q
```

Expect ~250 tests green (the docs recorded 247–250 passing as of the last live run).
If `.env` is missing `GEMINI_API_KEY` / `FALLBACK_LLM_API_KEY`, that's fine — the
agent has a deterministic heuristic fallback, which is exactly what Phase 2 tests
first.

---

## Phase 2 — Prove the AI agent (plan / guide / heal)

```bash
# 2.1 ✅ offline, no API key, no DB — proves the agent logic itself
pytest -q tests/test_agent.py tests/test_agent_wiring.py tests/test_mapping_fallback.py

# 2.2 ✅ run the planner against a real source table, offline heuristic
LLM_PROVIDER_ORDER=heuristic \
LRMIS_TARGET_PG_DSN=postgresql://postgres:postgres@localhost:5432/lrmis_target \
CENTRAL_DB_URL=postgresql://postgres:postgres@localhost:5433/central \
python scripts/agent.py --plan --source-table users --target-engine postgres --target-tables user,user_type

# 2.3 ✅ (optional but worth doing) same command with a free Gemini/Groq key set,
#        to see the AI actually reason about the mapping instead of name-matching
GEMINI_API_KEY=... LLM_PROVIDER_ORDER=gemini,fallback,heuristic \
python scripts/agent.py --plan --source-table users --target-engine postgres
```

What to look for: `risks` lists unmapped/low-confidence columns, `auto_ok` is `false`
whenever review is needed, exit code `3` = needs human review. This is the "propose
and guide" behavior — confirm it's not just returning an empty/trivial plan.

---

## Phase 3 — Prove direct source→target delivery, bypassing staging, for ONE entity

This is the concrete answer to "is staging removable" — right now 0 of 124 entities
use this path, so proving it on one real entity is the actual next step, not the
staging deletion itself (that's still gated behind section 1–4 of
`simplify-source-to-target-delivery`, don't run those tasks yet).

```bash
# 3.1 ✅ see current state (should show ~124 legacy, 0 on target when you start)
python scripts/lrmis_cutover.py

# 3.2 ✅ pick one already-deployed entity, e.g. <T> = authors (already pilot-verified per docs)
python -m src.pipeline propose --source-schema irimsv --source-table <T> --target-system LRMIS
#     -> note the PROPOSAL_ID

# 3.3 ✅ review what the AI proposed
python -m src.pipeline review --proposal <PROPOSAL_ID>

# 3.4 ⚠️ resolve any low-confidence / unmapped required columns it flags
python -m src.pipeline resolve --proposal <PROPOSAL_ID> \
    --source-column <src_col> --target-column <lrmis_col> --transform none --resolved-by admin

# 3.5 ⚠️ deploy to the TARGET path (this is the one that actually flips the entity off staging)
python -c "from src.services.lrmis_onboarding import deploy_to_lrmis; import json; print(json.dumps(deploy_to_lrmis(<PROPOSAL_ID>, 'admin'), default=str))"

# 3.6 ⚠️ push its current rows through the direct path
python -m src.pipeline backfill --entity <T>
python -m src.worker            # one delivery pass

# 3.7 ✅ confirm it landed on the target path, not staging
python scripts/lrmis_cutover.py
```

Success = `<T>` now shows under "on LRMIS target" with rows delivered and empty
quarantine. That's proof the direct path works on real data — but it's one entity,
not a signal to delete staging (the other 123 are still relying on it).

---

## Phase 4 — Prove schema/dialect flexibility (the MySQL↔Postgres swap)

This exercises the part you specifically asked about — target schema changes and
cross-dialect mapping.

```bash
# 4.1 ✅ restore the alternate Postgres schema (already used in the last live run)
createdb lrmis_target_alt
pg_restore --no-owner --no-privileges -d lrmis_target_alt "C:/Users/deped/Documents/lrmis-main/lrmis_db/old-lrmis.backup"

# 4.2 ✅ dry-run: discover the new schema, diff it against what's approved, list affected entities
LRMIS_TARGET_PG_DSN=postgresql://postgres:postgres@localhost:5432/lrmis_target_alt \
python scripts/schema_swap.py --target-engine postgres --dry-run

# 4.3 ⚠️ only if the dry-run diff looks right: confirmed apply (re-maps affected entities,
#        human-gated on any low-confidence result, then recreates + re-delivers)
python scripts/schema_swap.py --target-engine postgres --confirm lrmis_target_alt
```

Success = the dry-run correctly lists which entities the schema change affects, and
(if you run --confirm) the AI re-proposes mappings for just those entities without
touching the unaffected ones. This is the live-tested MySQL→Postgres / schema-swap
capability from `generic-ai-db-migration-engine`.

---

## What NOT to do yet

- Don't run `simplify-source-to-target-delivery` section 5 (delete staging) or touch
  `src/worker.py`'s legacy routing — sections 1–2 of that plan (migrate everything,
  collapse the worker) aren't done, and staging is still the only thing delivering
  data for 123 of 124 entities.
- Don't run `nightly_refresh.py --confirm ... --restore` against your real dump until
  Phase 0.2's UTF-16 encoding issue (noted in `docs/RUNBOOK_source_to_target.md`) is
  fixed — `psql` can't read the dump as-is.

## After you run this

Come back with what Phase 2–4 actually showed (pass/fail, any error output) and I'll
help debug specific failures or decide the next concrete step — e.g., scaling Phase 3
to more entities, or drafting the `PostgresSourceAdapter` that's still missing from
the generic engine.
