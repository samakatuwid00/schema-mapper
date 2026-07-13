# Runbook — test the AI-agentic system now

The migration agent (`src/agent/`) plans, guides, and heals a migration. It is
**schema-only** (never sends row values) and **human-gated** (low-confidence
proposals pause). You can exercise it two ways.

## 0. Fastest check — it already works offline, no API key, no DB

The whole AI layer runs on a deterministic **heuristic** fallback when no LLM is
configured, so the agent is testable with zero setup:

```bash
pytest -q tests/test_agent.py tests/test_agent_wiring.py tests/test_mapping_fallback.py
```

These prove: the planner grades risks and gates low-confidence mappings; guide
resolves unmapped-column / type-mismatch / FK dilemmas; heal is proposal-only
unless autonomous; prompts carry no row values; and the heuristic fallback maps
by name offline.

## 1. Run the agent planner on a real source table

Needs a **target** to discover (a Postgres DB — e.g. `old-lrmis.backup` restored,
see below) and **central** (Postgres holding the source tables under schema
`irimsv`). Point them via env, then:

```bash
# OFFLINE (no API key) — deterministic name-matching, everything low-confidence:
LLM_PROVIDER_ORDER=heuristic \
LRMIS_TARGET_PG_DSN=postgresql://postgres:postgres@localhost:5432/lrmis_target \
CENTRAL_DB_URL=postgresql://postgres:postgres@localhost:5433/central \
python scripts/agent.py --plan --source-table users \
    --target-engine postgres --target-tables user,user_type

# WITH free AI (better mappings) — set a free Gemini or Groq key, then:
GEMINI_API_KEY=... LLM_PROVIDER_ORDER=gemini,fallback,heuristic \
python scripts/agent.py --plan --source-table users --target-engine postgres
```

Output: the proposed per-column mapping, `risks` (unmapped / low-confidence),
and `auto_ok` (whether it could deploy without review). Exit code `3` means the
plan needs human review, `0` means it is clean.

## 2. Restore the Postgres target (old-lrmis.backup) so it can be discovered

`old-lrmis.backup` is a PostgreSQL custom-format archive — restore it into a
Postgres DB, then the adapter discovers its schema (the binary is never parsed):

```bash
createdb lrmis_target
pg_restore --no-owner --no-privileges -d lrmis_target "C:/Users/deped/Documents/lrmis-main/lrmis_db/old-lrmis.backup"
```

Then a schema-swap dry-run previews what a swap would re-map (read-only):

```bash
LRMIS_TARGET_PG_DSN=postgresql://postgres:postgres@localhost:5432/lrmis_target \
python scripts/schema_swap.py --target-engine postgres --dry-run
```

## 3. Provider setup (all free tiers)

| var | purpose |
|---|---|
| `GEMINI_API_KEY` | free Gemini key (aistudio.google.com) |
| `FALLBACK_LLM_API_KEY` (+ `_BASE_URL`, `_MODEL`) | free Groq/Cerebras/OpenRouter/Mistral |
| `LLM_PROVIDER_ORDER` | try order; add `heuristic` last so a quota wall never fails a run; use `heuristic` alone to run offline |

Prompts are schema-only — column names, types, nullability. **No row values are
ever sent to any provider** (`tests/test_mapping_fallback.py::test_prompt_is_schema_only_never_row_values`).

## What the agent does at each step

- **plan** (`MigrationAgent.plan`) — schema-only, grades risks, sets `auto_ok`.
- **guide** (`.guide`) — on a blocked deploy, offers resolution options with a
  recommendation (wired into `deploy_to_lrmis(agent=…)` → `needs_guidance`).
- **heal** (`.heal`) — on a delivery error, proposes a fix; applies only the safe
  string→int cast and only when `autonomous_heal=True` (wired into the worker).
- Every action is auditable to `integration.onboarding_audit` (`performed_by='agent'`).
