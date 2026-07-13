## Why

The current pipeline is tightly coupled to a specific source (PostgreSQL IRIMSV) and target (MySQL LRMIS with 51 tables whose structure is parsed from one hardcoded DDL file, `lrmis.sql`). Two forces make that coupling a problem *now*, not hypothetically:

1. **The plan changed to selective, schema-driven delivery.** We no longer migrate every source entity — we deliver only the specific entities the target needs (already how Path B's opt-in model works). What is missing is the ability to **swap the target schema itself** and have the system re-map the affected entities automatically. The concrete trigger: the target structure is moving to the schema in `C:\Users\deped\Documents\lrmis-main\lrmis_db\old-lrmis.backup`, a PostgreSQL `pg_dump` **custom-format (binary) archive** that the current MySQL-DDL-text parser (`LrmisRegistry.from_sql_file`) cannot read at all. Today a target-schema change is only *detected* (`src/services/lrmis_schema.py` fingerprints the DDL and lists affected entities); re-creating the target and re-mapping every deployed entity is manual, one entity at a time.

2. **The AI mapping is the reusable product; the plumbing is not.** The mapping engine (`src/mapping_engine.py`), transform pipeline (`src/transform_engine.py`), and onboarding workflow (discover → propose → deploy → sync) are already engine-agnostic. Abstracting connectors, types, and dialect into a generic engine turns the AI-powered mapping into a product that works between *any* source→target pair, and lets an embedded AI agent guide a database manager from planning through self-healing.

This change generalizes the engine-specific parts into pluggable adapters **and** delivers the one-command **schema-swap + re-map** flow the plan now requires — built on the AI mapping layer that already exists and already runs on free AI APIs.

## AI provider strategy (free-tier first)

The engine must run on **free AI APIs**, so the AI layer is provider-agnostic by design — and the mapping step already is. `propose_mapping()` tries the providers named in `LLM_PROVIDER_ORDER` (default `gemini,fallback`), skips unconfigured ones, and **fails over on any error, including quota `429`, with `Retry-After` backoff**. Configured providers today: Gemini (`gemini-2.5-flash`) plus any OpenAI-compatible endpoint — `groq`, `cerebras`, `openrouter`, `mistral` — all of which offer a free tier. The AI agent reuses this exact layer instead of hardwiring one vendor.

Free tiers change three things, which are **requirements**, not nice-to-haves:

- **Privacy — schema-only prompts.** Free tiers may train on submitted data, and LRMIS holds real DepEd PII. The AI layer sends only **schema metadata** (table names, column names, generic types, FK structure) — **never row values**. The planner does not receive sample rows; when a value-shape hint is needed it is derived locally (e.g. redacted/synthetic cardinality stats), not sent verbatim.
- **Rate limits over cost.** The budget is RPM/RPD/TPM, not dollars. The engine caches accepted proposals (keyed by source+target schema fingerprint) so a schema that has not changed never re-calls the API, paces bulk re-mapping under the provider's limits, and degrades to a **deterministic name/type heuristic** matcher when every provider is quota-exhausted — the run continues and flags heuristic mappings for review rather than failing.
- **Autonomy limits.** Free/smaller models are reliable for schema mapping but not for unattended destructive fixes. Guide and heal produce **proposals only, human-gated**, on the free tier; autonomous apply stays opt-in and off by default.

## What Changes

- **Abstract `SourceAdapter` / `TargetAdapter` protocols** — extract Postgres/MySQL into concrete implementations behind a common interface; adapters expose `discover_schema()` so the schema comes from the live/restored database, not a hardcoded DDL file.
- **Generic type system** — bridge native types through a generic intermediate (`varchar → GenericType.STRING → TEXT`), so cross-engine (incl. MySQL→Postgres) mapping is reliable.
- **Dialect-aware SQL builder** — parameterized DML/DDL per target engine (not hardcoded PostgreSQL `gen_random_uuid()`).
- **Dynamic schema registry** — built from any adapter's `discover_schema()`, replacing the hardcoded `lrmis.sql` parse. This is also how the target schema in `old-lrmis.backup` is ingested: **restore the backup, discover its `information_schema`** (reusing the existing `LrmisRegistry.from_information_schema` live-DB path) — the binary archive is never parsed directly.
- **One-command schema-swap + re-map** (the flow the plan now needs): point the engine at a new target schema → rebuild the registry from adapter discovery → **diff** old vs new structure → for each *affected* deployed entity, the agent re-proposes mappings (schema-only, free-tier, human-gated on low confidence) → recreate the target → re-deliver only the entities we keep. Reuses the DDL fingerprint/drift + `redeploy_plan()` already in `src/services/lrmis_schema.py`; the destructive recreate/re-deliver stays an explicit, confirmed step.
- **Provider-agnostic AI agent** — a planner, interactive guide, and self-healer built on the existing multi-provider `propose_mapping` failover (free-tier first), not tied to Gemini. All actions are proposals; destructive actions require confirmation.
- **Pluggable CDC** — trigger-based, debezium, or polling strategies.
- **Selective delivery stays first-class** — delivery remains opt-in per entity; migrating every entity is never required. (Optional, out of the first slice: row-level delivery filters.)
- **BREAKING**: `lrmis_registry.py`, `lrmis_writer.py`, `connectors.py` are restructured into generic abstractions.
- **BREAKING**: LRMIS-specific domain logic (`APP_ASSIGNED_ID_TABLES`, `station` FK resolution, `id_sequence`, `delivery_audit`) moves into a config/plugin, not core.

## Capabilities

### New Capabilities
- `generic-adapters`: Pluggable source/target connector interfaces (Postgres, MySQL, MSSQL, Snowflake, CSV, API) exposing `discover_schema()` so target structure is read from the live/restored database.
- `ai-mapping-engine`: Schema-agnostic, **provider-agnostic** column mapping across any source→target pair, running free-tier-first with automatic failover, proposal caching, and a deterministic fallback matcher. Schema-only prompts.
- `ai-agent`: Embedded, provider-agnostic agent for migration planning, interactive guidance, and self-healing — proposals only, human-gated on the free tier.
- `dialect-system`: Generic type system + dialect-aware SQL generation across engines.
- `schema-swap-remap`: One command to adopt a new target schema (including from a `pg_dump`/`.backup` restored into a live DB), diff it against the approved structure, AI-re-map only the affected deployed entities (human-gated), recreate the target, and re-deliver the kept entities — building on the existing DDL fingerprint/drift detection.

### Modified Capabilities
- `migration-management`: Expand from Postgres-centralized migration tracking to multi-engine migration orchestration.
- `schema-observability`: Extend drift detection to any source/target adapter pair, and link a detected target-schema change to the `schema-swap-remap` action instead of stopping at "drifted".

## Impact

- **Core**: `src/connectors.py` → adapter protocols + Postgres/MySQL implementations. `src/lrmis_registry.py` → dynamic registry from `discover_schema()` (the `from_information_schema` path becomes the primary schema source; DDL-file parse becomes an optional convenience). `src/lrmis_writer.py` → dialect-aware writer. New `src/adapters/`, `src/dialect/`, `src/agent/` packages.
- **AI layer (reuse, don't rebuild)**: the agent wraps the existing multi-provider `src/mapping_engine.py` failover; no new hard dependency on Gemini. Enforce schema-only prompts (drop any `source_sample` row payload). Add a proposal cache and a heuristic fallback matcher.
- **Schema-swap + re-map**: new orchestration reusing `src/services/lrmis_schema.py` (fingerprint/drift/`redeploy_plan`), adapter `discover_schema()`, `propose_mapping()` per affected entity, and `scripts/init_lrmis_target.py` + `refresh_entity` for the confirmed recreate/re-deliver.
- **CLI**: new commands `sync-engine init | plan | run | agent`, plus `sync-engine schema-swap` (dry-run diff → gated re-map → confirmed apply).
- **Config**: engine-level config (adapters, connections, type mappings, `LLM_PROVIDER_ORDER`) separate from per-entity mapping; LRMIS domain logic in an `lrmis_plugin` module.
- **Dependencies**: `google-genai` (existing) stays optional; free OpenAI-compatible providers need only a base URL + key. No new DB drivers (`psycopg2`, `mysql-connector` already present). MySQL→Postgres targets use the existing Postgres driver.
- **Docs**: migration guide (pipeline-specific → generic engine) and a schema-swap runbook covering the `.backup` restore-then-discover path.
- **Recommended sequencing**: ship `schema-swap-remap` first as a thin slice on the current code (adapter discovery for the new Postgres schema + existing fingerprint/drift + existing free multi-provider `propose_mapping`), then land the full adapter/dialect generalization. This delivers the plan's schema-swap need without waiting on the entire generic engine.
- **Out of scope (first slice)**: full MSSQL/Snowflake/CSV adapters, bidirectional sync, row-level delivery filters, and autonomous (non-gated) self-heal.
