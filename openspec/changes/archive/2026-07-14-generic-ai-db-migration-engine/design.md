## Context

The existing pipeline (`src/pipeline.py`, `src/connectors.py`, `src/lrmis_registry.py`, `src/lrmis_writer.py`) is purpose-built for one flow: PostgreSQL IRIMSV → MySQL LRMIS, with the target's 51 tables parsed from a single hardcoded DDL file (`lrmis.sql`). The AI mapping engine (`src/mapping_engine.py`), transform pipeline (`src/transform_engine.py`), and onboarding workflow (discover → propose → deploy → sync) are already engine-agnostic and are the core to preserve and generalize.

Two realities drive this design:

1. **The target is variable and must be discovered, never assumed.** The target structure is moving to the schema in `old-lrmis.backup` — a PostgreSQL `pg_dump` custom-format (binary) archive, i.e. a *different engine* from today's MySQL target. There will be more cases like this. The engine cannot hardcode "MySQL target" or parse one DDL file; it must adapt at runtime to whatever engine, schema, types, and dialect the connected target offers.
2. **Delivery is selective, and schema changes must re-map automatically.** We deliver only the entities the target needs (Path B's opt-in model). What is missing is a **schema-swap + re-map** flow: when the target schema (or engine) changes, re-discover it, diff it, AI-re-map only the affected deployed entities, recreate the target, and re-deliver the kept entities — human-gated, on **free AI APIs**.

This design restructures the engine-specific parts into pluggable adapters and adds the schema-swap flow, while keeping the existing IRIMSV→LRMIS path fully operational.

## Goals / Non-Goals

**Goals:**
- Define abstract `SourceAdapter` and `TargetAdapter` protocols any engine can implement; the **target engine, schema, and dialect are discovered from the connected adapter**, not assumed.
- Generic type system (`GenericType`) with bidirectional native-type mappings.
- Dialect-aware SQL builder generating engine-specific DML/DDL.
- Dynamic registry from any adapter's `discover_schema()`, replacing the hardcoded `lrmis.sql` parse. Ingest a `pg_dump`/`.backup` target by **restoring it into a live DB and discovering `information_schema`** — never by parsing the binary archive.
- **Schema-swap + re-map** as one gated command: re-point target → re-discover → diff → AI-re-map affected entities → recreate → re-deliver kept entities.
- **Provider-agnostic, free-tier-first AI** reusing the existing multi-provider failover; **schema-only prompts (never row values)** for PII safety.
- **Interactive AI agent** in the project: planner, interactive guide, and (gated) self-healer.
- Keep the existing IRIMSV→LRMIS path working via `PostgresSourceAdapter` + `MySQLTargetAdapter`.

**Non-Goals:**
- NOT building a new UI console (stays CLI + existing admin API/web).
- NOT bidirectional sync (source→target only).
- NOT unattended destructive apply — schema-swap recreate/re-deliver and any self-heal that mutates data are explicit, confirmed, human-gated (especially on free-tier models).
- NOT sending row-level data to any AI provider.
- NOT (first slice) full MSSQL/Snowflake/CSV adapters or row-level delivery filters.

## Decisions

### Decision 1: Protocol-based adapters (not ABC inheritance)

Use `typing.Protocol` for adapter interfaces so implementations are duck-typed and installable as third-party packages without forking core.

```python
class TargetAdapter(Protocol):
    engine_type: str                                  # "postgres" | "mysql" | ...
    def discover_schema(self) -> Schema: ...          # source of truth for structure
    def dialect(self) -> Dialect: ...                 # engine-specific SQL
    def recreate_schema(self, schema: Schema) -> None: ...
    def get_pk_columns(self, table: str) -> list[str]: ...
```

**Rationale**: `discover_schema()` on the adapter is what makes the target *discovered, not assumed* — the same code path serves a MySQL target today and a Postgres target tomorrow.

### Decision 2: GenericType enum with native-type mappings

```python
class GenericType(Enum):
    STRING = "string"; INTEGER = "integer"; FLOAT = "float"; BOOLEAN = "boolean"
    DATE = "date"; DATETIME = "datetime"; BINARY = "binary"; JSON = "json"; UUID = "uuid"
```

Each adapter declares `native_to_generic(col_type) → GenericType` and `generic_to_ddl(generic_type, col) → str`. The AI prompt receives `GenericType` names, not raw `varchar(255)`, so mapping accuracy is engine-independent.

### Decision 3: Dialect-aware SQL builder

`Dialect` protocol (quote, upsert, insert, update, bulk insert, truncate, uuid) with `MySQLDialect`, `PostgresDialect`, `MSSQLDialect`, and a `raw(sql)` escape hatch. The adapter hands back the right dialect, so the writer and refresh path are engine-agnostic (no hardcoded `gen_random_uuid()`).

### Decision 4: Dynamic schema registry from adapter (and from a restored backup)

```python
class SchemaRegistry:
    def __init__(self, adapter: TargetAdapter): ...
    def load(self): ...                    # from adapter.discover_schema()
    def foreign_keys(self, table) -> list[ForeignKey]: ...
    def topological_order(self) -> list[str]: ...
```

Replaces `LrmisRegistry`'s hardcoded DDL-file parse. The FK graph + topological sort logic is reused as-is. For a `.backup`/`pg_dump` target the flow is **restore → `discover_schema()`** (the existing `LrmisRegistry.from_information_schema` live-DB path is the seed for this); the binary is never parsed.

### Decision 5: Pluggable CDC via strategy classes

`CDCStrategy` protocol with `TriggerCDC` (existing Postgres trigger → `integration.outbox`, default for the current source), `PollingCDC` (any engine with an `updated_at` column), and `DebeziumCDC`. CDC is optional; default falls back to polling.

### Decision 6: AI agent — provider-agnostic, free-tier, interactive

The agent is an interactive loop integrated into CLI and worker, built on the **existing** multi-provider mapping layer (`src/mapping_engine.py` `propose_mapping`), not tied to any one vendor.

```python
class MigrationAgent:
    def plan(self, source_schema: Schema, target_schema: Schema) -> MigrationPlan: ...  # schema only
    def guide(self, dilemma: Dilemma) -> Guidance: ...    # interactive
    def heal(self, error: SyncError, context: SyncContext) -> HealProposal: ...  # gated
```

- **Planner** (one-shot): source + target **schema metadata** → suggested mappings, risks, confidence. No sample rows.
- **Guide** (interactive — the "interactive AI"): when a deploy is blocked, presents the DBA with natural-language options and applies the chosen one. Runs in a `sync-engine agent` session.
- **Healer** (gated): on a delivery error, proposes a fix and, only if autonomous-apply is explicitly enabled, applies + retries; otherwise quarantines and asks. Default on free tier: propose-only.

**Provider strategy (free-tier first).** Reuse `LLM_PROVIDER_ORDER` (default `gemini,fallback`) with automatic `429` failover across Gemini `2.5-flash` and OpenAI-compatible free tiers (`groq`, `cerebras`, `openrouter`, `mistral`). Layer on:
- **Schema-only prompts** — table/column names, generic types, FK structure. Never row values (LRMIS holds DepEd PII; free tiers may train on submissions).
- **Proposal cache** keyed by source+target schema fingerprint — an unchanged schema never re-calls the API.
- **Deterministic fallback** — a name/type heuristic matcher runs when every provider is quota-exhausted, so a bulk run completes (heuristic mappings flagged for review) instead of failing.

**Rationale**: The interactive guide + gated heal are the differentiator; running provider-agnostic on free tiers with schema-only prompts makes it affordable and safe for a government dataset.

### Decision 7: Schema-swap + re-map flow

A single gated command (`sync-engine schema-swap`) composes existing machinery:

1. **Re-point** the target adapter (possibly a new engine — e.g. MySQL→Postgres from `old-lrmis.backup`).
2. **Re-discover** the new schema via `adapter.discover_schema()`; rebuild the registry.
3. **Diff** the new structure against the last approved fingerprint (reuse `src/services/lrmis_schema.py` fingerprint/drift + `redeploy_plan()`), producing the list of *affected deployed entities*.
4. **Re-map** only the affected entities with the agent/`propose_mapping` (schema-only, cached); human-gate mappings below the confidence threshold.
5. **Apply (confirmed, destructive)**: recreate the target from the new schema, re-seed FK-closure lookups, and re-deliver only the kept (selective) entities via `refresh_entity`.

Steps 1–4 are non-destructive (dry-run diff + proposed re-maps). Step 5 is explicit and confirmed.

### Decision 8: LRMIS-specific logic → config plugin

Move `APP_ASSIGNED_ID_TABLES = {"station"}`, `DEFAULT_ID_SEQUENCE_START`, station FK resolution, and the `delivery_audit` table into `src/adapters/lrmis_plugin.py`. Core has no knowledge of `station` or LRMIS; a different target loads a different plugin (or none).

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| **PII leakage to a free AI tier** | Schema-only prompts enforced at the prompt-builder; no row values reach any provider. Prompt payloads are unit-tested to contain no sample data. |
| **Free-tier rate limits stall a bulk re-map** | Proposal cache (fingerprint-keyed) + `429` failover across providers + deterministic heuristic fallback so the run completes. |
| **Small/free model gives a wrong or unsafe fix** | All agent actions are proposals; confidence threshold (default 0.7); destructive apply and autonomous heal are opt-in and off by default. |
| **Target engine differs from source (MySQL↔Postgres)** | Generic type system + dialect isolate engine specifics; the target adapter supplies both. A `PassthroughAdapter` skips conversion when source=target engine. |
| **`.backup` is a binary archive the parser can't read** | Never parse it — restore into a live DB and `discover_schema()`. |
| **Adapter abstraction overhead for simple migrations** | `PassthroughAdapter`; dialect `raw()` escape hatch. |
| **Bulk row throughput in Python** | `executemany` + batched INSERT; adapter `bulk_import(file, format)` for native tools. |

## Migration Plan

**First slice (ship this to satisfy the new plan) — schema-swap on the current code:**
1. Add a `TargetAdapter` for the new target engine (Postgres) with `discover_schema()` + `dialect()`; keep the MySQL target working.
2. Wire `SchemaRegistry` to `discover_schema()` (make `from_information_schema` the primary path).
3. Implement `sync-engine schema-swap` reusing `lrmis_schema.py` fingerprint/drift + `propose_mapping` (free multi-provider) + `init_lrmis_target.py`/`refresh_entity`.
4. Enforce schema-only prompts; add the proposal cache + heuristic fallback.
5. Regression-test the existing IRIMSV→LRMIS path.

**Full generalization (after the slice):**
6. Extract `SourceAdapter`/`TargetAdapter` protocols; move `PostgresCentralConnector`/`MySQLStagingConnector` into `src/adapters/`.
7. Build the generic type system + dialects; port `generate_refresh_sql`.
8. Wire the full AI agent (`plan`/`guide`/`heal`) and the interactive `sync-engine agent` session.
9. Refactor the writer to dialect + registry; extract the LRMIS plugin.
10. Add MSSQL/Snowflake/CSV adapters in priority order.

Rollback: the old pipeline is untouched during development; the generic engine runs alongside and is selected by config. Revert by switching module paths back.
