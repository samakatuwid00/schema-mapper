<!-- LIVE RUN 2026-07-13: restored old-lrmis.backup (PG17 dump) into an isolated
`oldlrmis` DB in the central PG16 server via a postgres:17 pg_restore container.
Schema is `lrmis` (66 base tables + 48 views). VERIFIED LIVE: PostgresTargetAdapter
discovery (66 tables, FK graph, topo sort), generic_to_ddl created delivery_audit,
GenericWriter insert+RETURNING+str→int cast round-tripped in real Postgres.
FIXED (Issue A): discovery counted views as tables → `_COLS_SQL` now filters
table_type='BASE TABLE' (66 not 114). FINDING (Issue B): old-lrmis PKs are plain
int (no identity/serial) → 65/66 tables need app-assigned ids to receive NEW rows;
the writer supports this via plugin `app_assigned_id_tables`, but the LRMIS plugin
lists only `station` — a real target needs a broader app-assigned set (plugin work).
UPDATE: Issue B RESOLVED — added `OLD_LRMIS` plugin (`app_assign_non_autoincrement`)
+ plugin-driven writer classification (`_is_app_assigned`/`_is_reference`); live-
verified on real Postgres: OLD_LRMIS app-assigned a plain-int-PK table (allocated
id 1000000001, inserted). AI ALSO run live: LLM keys ARE configured here
(LLM_PROVIDER_ORDER=groq,cerebras,gemini); `MigrationAgent().plan()` ran against
Groq (free tier) and returned a confident mapping.
MULTI-ENTITY LIVE RUN done: 3 source entities (irimsv authors/grade_levels/
divisions) → old-lrmis via LIVE AI mapping + GenericWriter. authors→author 3/3
rows delivered into real Postgres (source-carried uuid ids). Added a
"source-carried id" writer path (mapping supplies the PK ⇒ insert with it, before
app-assigned/reference) + reference-only-first classification. Live findings
(guardrails working): AI proposed a bad str→int transform on a uuid FK
(grade_levels) → build_values_by_table QUARANTINED the row (0 delivered, no bad
data); divisions→region hit a DB UniqueViolation (semantic mismatch) → rolled
back. Bad AI mappings never landed. Full chain live-verified end to end. -->

## 0. First slice — schema-swap + re-map on the current code

- [x] 0.1 Add `TargetAdapter` protocol + `MySQLTargetAdapter` + `PostgresTargetAdapter` with `discover_registry()`; keep MySQL target working (`src/adapters/`). (`dialect()` tracked in §4.)
- [x] 0.2 Make `LrmisRegistry` load from discovery — added FK-aware `from_discovery()`; `from_information_schema()` now pulls FKs via the adapter so the discovered registry can order inserts parents-first
- [x] 0.3 Ingest a `.backup`/`pg_dump` target by restore-then-discover — `PostgresTargetAdapter` reads a restored DB + guarded `restore_pg_backup` helper (`src/services/pg_restore.py`, configured-not-guessed, PGDMP magic check, dry-run plan)
- [x] 0.4 Implement `schema-swap --dry-run` — `src/services/schema_swap.py` (discover new registry → diff vs approved → list affected deployed entities) + `scripts/schema_swap.py` CLI. (Actual AI re-map proposals deferred to §0.5/§8; dry-run reports `would_remap`.)
- [x] 0.5 Implement `schema-swap --confirm` — `schema_swap.apply()` + CLI (typed-confirm guard): real AI re-map (schema-only via `propose_mapping`), **persist new mappings** (`persist_remap` → new approved proposal + `onboarding_field_review` rows, then `deploy_to_lrmis`), engine-dispatched recreate (mysql `init_lrmis_target` / postgres `pg_restore`), re-deliver kept entities (mysql `redeliver_all`). Order: gate → persist → recreate → redeliver. Postgres row delivery still reports `pending_generic_writer` (needs §7 writer). Live DB path unverified in this env; pure logic + wiring unit-tested
- [x] 0.6 Human-gate — apply blocks (`status: blocked_on_review`) and changes nothing when any re-mapping is below `--threshold`, unless `--force`; tested
- [x] 0.7 Regression-test the existing IRIMSV→LRMIS path is unaffected — full suite green (225 passed), incl. original writer/delivery/nightly/onboarding suites

## 1. Provider-agnostic, free-tier AI + privacy

- [x] 1.1 Multi-provider failover in `propose_mapping` (`LLM_PROVIDER_ORDER`, `429` backoff); agent reuses it (no hardwired Gemini)
- [x] 1.2 **Schema-only prompts** — removed the `sample_values` row-value leak from `_format_columns`; prompt-privacy test asserts no row values reach the prompt
- [x] 1.3 Proposal cache — `propose_mapping(cache=…)` keyed by source+target schema, skips the API when unchanged
- [x] 1.4 Deterministic `heuristic` fallback provider (`heuristic_mapping`) — terminal in `LLM_PROVIDER_ORDER` (or alone for offline); low-confidence so flagged for review
- [x] 1.5 Documented free-tier + offline setup in `.env.example` + `docs/RUNBOOK_agent.md`

## 2. Adapter Protocols

- [x] 2.1 `SourceAdapter` + `TargetAdapter` protocols in `src/adapters/_protocols.py`
- [x] 2.2 `PostgresSourceAdapter` (`src/adapters/postgres_source.py`) — discover_schema/count/pk + batched `fetch_rows` streaming; LIVE-verified vs central irimsv (52 tables, authors 1459 rows streamed)
- [x] 2.3 `MySQLTargetAdapter` (`src/adapters/mysql_target.py`)
- [x] 2.4 `PostgresTargetAdapter` (`src/adapters/postgres_target.py`) — covers the `old-lrmis` Postgres target
- [x] 2.5 `PassthroughAdapter` (`src/adapters/passthrough.py`) + `same_engine()` — same-engine passthrough marker (skip native→generic→native round-trip)
- [x] 2.6 Adapter factories `get_target_adapter` + `get_source_adapter`

## 3. Generic Type System

- [x] 3.1 Define `GenericType` enum in `src/dialect/types.py`
- [x] 3.2 Implement `native_to_generic()` (mysql + postgres maps)
- [x] (bonus, closes the type-casting gap) `cast_value()` in `src/dialect/types.py`, wired into `GenericWriter._cast_row` so cross-engine value mismatches (numeric string → INTEGER col) are coerced before insert
- [x] 3.3 `generic_to_ddl()` per dialect + `create_table_sql()` (`src/dialect/builder.py`); `src/delivery/schema.py` `create_target_ddl`/`create_delivery_audit_sql`/`create_target_schema`. Verified LIVE: created delivery_audit on real restored old-lrmis Postgres
- [~] 3.4 Port `_infer_column_type()` — DEFERRED: it's legacy single-table-staging code (`pipeline.py:151`, MySQL DDL for `irimsv_*_staging`) that the generic `generic_to_ddl` already supersedes; porting adds regression risk to the being-removed legacy path for no new capability
- [x] 3.5 Feed `GenericType` into the AI prompt — `_format_columns` annotates each column with its engine-independent `GenericType` via `native_to_generic_any` (additive, keeps the native type; no PII); tested

## 4. Dialect-aware SQL Builder

- [x] 4.1 Define `Dialect` protocol in `src/dialect/builder.py` (with `raw()` escape hatch)
- [x] 4.2 Implement `MySQLDialect` (backtick quoting, `ON DUPLICATE KEY UPDATE`)
- [x] 4.3 Implement `PostgresDialect` (`gen_random_uuid()`, `ON CONFLICT`)
- [x] (bonus) Generic type system `src/dialect/types.py` (`GenericType` + `native_to_generic` for mysql/postgres); adapters expose `.dialect()`
- [x] 4.4 `MSSQLDialect` — bracket quoting `[x]`, `MERGE` upsert, `?` placeholder, `NVARCHAR(MAX)`/`UNIQUEIDENTIFIER` DDL, `NEWID()`; registered in `_DIALECTS`; tested
- [ ] 4.5 Port `generate_refresh_sql()` from `src/fast_refresh.py` to use the dialect (invasive — MySQL-specific legacy; deferred)
- [ ] 4.6 Port `target_fetch_sql()` from `src/connectors.py` to use the dialect (invasive; deferred)

## 5. Dynamic Schema Registry

- [x] 5.1 `SchemaRegistry(adapter)` (`src/schema_registry.py`) — `load()` from `adapter.discover_registry()`, lazy, `from_registry()`; tested
- [x] 5.2 FK graph + topological sort delegated to `LrmisRegistry` (already implemented there)
- [~] 5.3 Facade added; full deprecation of direct `get_registry()`/`LrmisRegistry` imports across the codebase deferred (invasive, wide blast radius)

## 6. Pluggable CDC

- [x] 6.1 `CDCStrategy` protocol (`src/cdc/_protocols.py`)
- [x] 6.2 `TriggerCDC` — reads undelivered `integration.outbox` (existing trigger path)
- [x] 6.3 `PollingCDC` — `WHERE <ts> > ?` for any engine with a timestamp column; safe-identifier guarded; tested
- [x] 6.4 `DebeziumCDC` — injectable Kafka consumer wrapper (raises until wired)
- [x] 6.5 `get_cdc_strategy({strategy, options})` config factory; tested (`tests/test_cdc.py`)

## 7. Generic Writer (replaces lrmis_writer.py)

- [x] 7.1 Build a dialect-aware multi-table writer with FK propagation — `src/delivery/writer.py` `GenericWriter.write_row()` (parents-first, `_apply_foreign_keys`, crosswalk idempotency); tested for Postgres (RETURNING) + MySQL (lastrowid)
- [x] 7.2 FK/reference/app-assigned logic driven by adapter registry + config (`app_assigned_tables`/`id_start` are constructor params, not hardcoded — §8 plugin direction); `_resolve_reference_id` is dialect-aware
- [x] 7.3 Dialect-aware insert/update/insert-returning + generated-id retrieval (`dialect.supports_returning` → RETURNING vs `cursor.lastrowid`). Generic type casting now applied via `_cast_row`/`cast_value` (see §3 bonus)
- [x] 7.4 Add `truncate_and_rebuild()` using dialect `truncate_sql()` + reverse topological order (skips seeded reference tables)
- [x] 7.5 Wire `GenericWriter` into the delivery/refresh path — `writer` seam on `refresh_entity`/`resolve_cross_entity_fks` (`_LegacyWriter` default = MySQL, unchanged); dialect-aware `row_exists`; `redeliver_all`/`_redeliver_entity` accept `target`+`writer`+`registry`; `PostgresTargetAdapter.connection()`; `schema_swap._default_redeliver` delivers into a Postgres target via `GenericWriter`. Live-DB path unverified in this env; seam + routing unit-tested
- [x] 7.6 Worker **streaming** path now engine-generic — `deliver_event(writer=…)` routes row writes through the writer and the delivery-audit through `writer.record_delivery_audit` (dialect-aware upsert on `GenericWriter`); `process_once._open_target` selects target+writer by `LRMIS_TARGET_ENGINE` (default MySQL, unchanged; `postgres` → `PostgresTargetAdapter` + `GenericWriter`). Opt-in via env; tested. Needs `delivery_audit` to exist in the Postgres target (target-setup / `generic_to_ddl`)

## 8. AI Agent (interactive)

- [x] 8.1 Implement `MigrationAgent.plan()` — schema-only planner over `propose_mapping` (multi-provider), grades risks + `auto_ok` (`src/agent/agent.py`)
- [x] 8.2 Implement `MigrationAgent.guide()` — interactive dilemma resolver (unmapped column → `best_match` auto-suggest; type_mismatch → cast; fk_violation → quarantine)
- [x] 8.3 Implement `MigrationAgent.heal()` — gated error fixer (propose-only by default; `autonomous_heal` opt-in applies only the safe string→int cast)
- [x] 8.4 Confidence-threshold config + human gates (`threshold`; low-confidence → `auto_ok=False`)
- [x] 8.5 `scripts/agent.py` — plan step of the agent session (discover source+target, print graded plan). Interactive REPL is a thin follow-up
- [x] 8.6 Wire guide into onboarding `deploy` (`deploy_to_lrmis(agent=…)` returns `needs_guidance` + `deploy_guidance()` instead of raising) and heal into the worker (`_deliver_path_b`/`process_once` opt-in `agent`; heal proposal annotates a delivery-error quarantine). Opt-in (`agent=None` unchanged); tested
- [x] 8.7 Audit agent actions to `integration.onboarding_audit` (`performed_by='agent'`) via `make_central_audit` (`src/agent/audit.py`); agent takes an injectable `audit` sink

## 9. LRMIS Plugin Extraction

- [x] 9.1 `APP_ASSIGNED_ID_TABLES` now sourced from `src/adapters/lrmis_plugin.py` (`LRMIS.app_assigned_id_tables`); `lrmis_writer` re-exports for back-compat
- [x] 9.2 station app-assigned/reference logic is config-driven in `GenericWriter` (injectable `plugin`; core holds no `station` knowledge)
- [x] 9.3 `DELIVERY_AUDIT_DDL` canonical copy lives in `lrmis_plugin`. NOTE: `scripts/init_lrmis_target.py` still has a legacy inline copy — dedupe pending
- [x] 9.4 `DEFAULT_ID_SEQUENCE_START` sourced from `LRMIS.id_sequence_start`
- [x] 9.5 Zero regression — full suite green (238 passed)

## 10. CLI Commands

- [x] 10.1 `sync-engine init` — writes an engine config template (`scripts/sync_engine.py`); tested
- [x] 10.2 `sync-engine plan` — delegates to `agent.py --plan`
- [~] 10.3 `sync-engine run` — dispatcher entry exists but returns "not implemented" (full backfill+CDC streaming deferred)
- [x] 10.4 `sync-engine schema-swap` — delegates to `schema_swap.py` (dry-run/apply)
- [x] 10.5 `sync-engine agent` — delegates to `agent.py` (plan CLI)
- [x] 10.6 Unified `sync-engine` CLI supersedes per-task `pipeline.py` commands (noted in help); dispatcher tested (`tests/test_sync_engine_cli.py`)

## 11. Testing & Docs

- [x] 11.1 Adapter protocol conformance tests (`test_adapter_conformance.py`) — target + source + passthrough satisfy their `Protocol`s
- [x] 11.2 Dialect unit tests (`test_dialect.py`) — MySQL + Postgres + MSSQL
- [x] 11.3 Registry discovery tests (`test_target_adapters.py`: from_discovery → FK graph → topological sort)
- [x] 11.4 Schema-swap tests (`test_schema_swap.py`: diff, affected-only re-map, gated apply, re-deliver)
- [x] 11.5 Prompt-privacy test (`test_mapping_fallback.py::test_prompt_is_schema_only_never_row_values`)
- [x] 11.6 Free-tier resilience test (`test_mapping_fallback.py`: unconfigured/failover → heuristic)
- [x] 11.7 Full regression — 277 passed
- [x] 11.8 Docs — `docs/GENERIC_ENGINE.md` (architecture, schema-swap flow, live old-lrmis example, adding a new engine) + `docs/RUNBOOK_agent.md` + `docs/RUNBOOK_source_to_target.md`
