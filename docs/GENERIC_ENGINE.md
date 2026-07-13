# Generic AI DB migration engine

This is the migration guide from the pipeline-specific IRIMSV→LRMIS(MySQL) code to
the generic, engine-agnostic engine (openspec change `generic-ai-db-migration-engine`).
The core innovation — AI-powered, schema-only mapping — is preserved; the plumbing
is now pluggable, so the same engine migrates between **any** source/target pair.

## Architecture

| Layer | Module | What it does |
|---|---|---|
| Source adapter | `src/adapters/postgres_source.py` | discover source schema, stream rows (`fetch_rows`) |
| Target adapter | `src/adapters/{postgres,mysql}_target.py` | discover the target schema/dialect from the live DB — **never a hardcoded DDL file** |
| Dialect | `src/dialect/` | per-engine SQL + `GenericType` + `generic_to_ddl`/`cast_value` (MySQL, Postgres, MSSQL) |
| Registry | `src/schema_registry.py` (+ `LrmisRegistry`) | FK graph, topological order, seed set — sourced from the adapter |
| Writer | `src/delivery/writer.py` `GenericWriter` | fan a source row across N target tables, dialect-aware, FK propagation, crosswalk idempotency |
| Plugin | `src/adapters/lrmis_plugin.py` | per-target domain config (app-assigned/reference tables, id range, audit) — core holds no `station`/LRMIS knowledge |
| CDC | `src/cdc/` | pluggable change capture (trigger / polling / debezium) |
| AI agent | `src/agent/` | schema-only, human-gated plan / guide / heal over the free-tier multi-provider mapping layer |
| CLI | `scripts/sync_engine.py` | `sync-engine {init,plan,schema-swap,agent,run}` |

## Key principles

- **The target is discovered, not assumed** — engine, schema, types, and dialect all
  come from the connected adapter. Re-pointing at a different engine is config.
- **Free-tier-first AI** — provider-agnostic failover (`LLM_PROVIDER_ORDER`), a
  `heuristic` offline fallback, a proposal cache, and **schema-only prompts** (never
  row values — the data may hold PII and a free tier may train on submissions).
- **Human-gated** — low-confidence mappings pause; agent heals are proposals.
- **Guardrails** — transform validation quarantines bad rows; the crosswalk makes
  delivery idempotent; the writer never TRUNCATEs.

## Adopting a new target schema (schema-swap)

```bash
# 1. restore the target (e.g. a pg_dump .backup) into a live DB, then:
python scripts/schema_swap.py --target-engine postgres --dry-run          # diff + affected entities
python scripts/schema_swap.py --target-engine postgres --confirm <db>     # re-map (gated) + recreate + re-deliver
```

The swap discovers the new schema, diffs it against the approved one, AI-re-maps only
the **affected** deployed entities (human-gated on low confidence), then recreates and
re-delivers the kept entities.

## Worked example — old-lrmis (Postgres), verified live

`old-lrmis.backup` is a Postgres `pg_dump` (schema `lrmis`, 66 base tables + 48 views).
Restore it, then:

- **Discovery** filters to base tables (66) and reads the FK graph.
- **PK strategy** — 65/66 tables have **string PKs carried from the source** (not
  DB-generated). So the `OLD_LRMIS` plugin sets `app_assign_non_autoincrement=False`
  and the writer's *source-carried-id* path inserts the mapped id; seeded lookups are
  listed in `reference_tables` (resolve-only). Only the lone int-PK table would use
  allocation.
- **Bulk delivery** — 1459 `irimsv.authors` → `lrmis.author` delivered in ~3s, and
  re-running is idempotent (crosswalk upsert).

## Adding a new engine

1. Implement a `TargetAdapter` (and/or `SourceAdapter`) in `src/adapters/`.
2. Add a `Dialect` in `src/dialect/builder.py` (`_DIALECTS`) + `generic_to_ddl` map.
3. If the target has domain-specific id/reference rules, add a `TargetPlugin`.
4. Register in `get_target_adapter` / `get_source_adapter`.

No core writer/mapping/agent code changes.
