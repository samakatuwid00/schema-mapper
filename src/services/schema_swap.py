"""Schema-swap dry-run + confirmed apply (generic engine, §0.4–0.5).

When the target schema (or engine) changes, this computes what a swap would do —
discover the new target structure, diff it against the currently approved
registry, and list only the deployed Path B entities the change affects — and
then, on a confirmed apply, re-maps those entities with the AI (schema-only,
human-gated on low confidence), recreates the target, and re-delivers the kept
entities.

Selective delivery is preserved throughout: entities not onboarded to the target
are never in scope. The dry-run is read-only; the apply is destructive and its
low-confidence gate blocks before anything is changed.
"""
from __future__ import annotations

import json
from dataclasses import asdict

import psycopg2.extras

from ..connectors import PostgresCentralConnector
from ..lrmis_registry import get_registry
from ..schema_models import Column, Schema, Table
from .common import ValidationError

TARGET_SYSTEM = "LRMIS"


# ---------------------------------------------------------------------------
# Pure diff helpers (engine-agnostic, no I/O)
# ---------------------------------------------------------------------------

def diff_registries(old, new) -> dict:
    """Structural diff of two registries: added/removed tables and, for tables
    present in both, added/removed/changed columns (type or nullability)."""
    old_t, new_t = set(old.table_names), set(new.table_names)
    changed_tables: dict[str, dict] = {}
    for name in sorted(old_t & new_t):
        oc = {c.name: c for c in old.get_table(name).columns}
        nc = {c.name: c for c in new.get_table(name).columns}
        added = sorted(set(nc) - set(oc))
        removed = sorted(set(oc) - set(nc))
        changed = sorted(
            c for c in (set(oc) & set(nc))
            if (oc[c].data_type, oc[c].nullable) != (nc[c].data_type, nc[c].nullable))
        if added or removed or changed:
            changed_tables[name] = {
                "added_columns": added,
                "removed_columns": removed,
                "changed_columns": changed,
            }
    return {
        "added_tables": sorted(new_t - old_t),
        "removed_tables": sorted(old_t - new_t),
        "changed_tables": changed_tables,
    }


def changed_table_set(diff: dict) -> set[str]:
    """Every table touched by the diff (added, removed, or column-changed)."""
    return (set(diff["added_tables"]) | set(diff["removed_tables"])
            | set(diff["changed_tables"]))


def _entity_target_tables(entity: dict) -> list[str]:
    value = entity.get("lrmis_target_tables")
    if isinstance(value, str):
        value = json.loads(value)
    return value or []


def affected_entities(diff: dict, entities: list[dict]) -> list[dict]:
    """Deployed entities whose target footprint intersects the changed tables."""
    changed = changed_table_set(diff)
    out: list[dict] = []
    for entity in entities:
        tables = _entity_target_tables(entity)
        hit = sorted(set(tables) & changed)
        if hit:
            out.append({
                "entity_id": entity.get("id"),
                "source_schema": entity.get("source_schema"),
                "source_table": entity.get("source_table"),
                "target_tables": tables,
                "affected_tables": hit,
            })
    return out


# ---------------------------------------------------------------------------
# AI re-map (schema-only, provider-agnostic) + confidence gate
# ---------------------------------------------------------------------------

def build_target_schema(registry, tables: list[str]) -> Schema:
    """A `schema_models.Schema` for the entity's target footprint, built from the
    discovered registry — metadata only (names, types, nullability, PK)."""
    return Schema(system_name=TARGET_SYSTEM, tables=[
        Table(name=t, columns=[
            Column(name=c.name, data_type=c.base_type, nullable=c.nullable,
                   is_primary_key=c.is_primary_key)
            for c in registry.get_table(t).columns
        ])
        for t in tables
    ])


def remap_entity(source_table: Table, target_schema: Schema, *,
                 threshold: float = 0.7, propose=None, client=None) -> dict:
    """Re-map one entity's columns against the new target schema via the AI.

    Uses the provider-agnostic `propose_mapping` (free-tier failover), which
    prompts with schema metadata only — no row values. Any column left unmapped
    or below `threshold` is flagged for human review (`auto_ok` is then False).
    """
    if propose is None:
        from ..mapping_engine import propose_mapping as propose
    mappings = propose(source_table, target_schema, client=client)
    needs_review = [
        {"source_column": m.source_column, "target_table": m.target_table,
         "target_column": m.target_column, "confidence": m.confidence}
        for m in mappings
        if m.target_table is None or m.target_column is None or m.confidence < threshold
    ]
    return {
        "source_table": source_table.name,
        "mappings": [asdict(m) for m in mappings],
        "needs_review": needs_review,
        "auto_ok": not needs_review,
    }


def remap_affected(affected: list[dict], new_registry, source_tables: dict, *,
                   threshold: float = 0.7, propose=None, client=None) -> list[dict]:
    """Re-map every affected entity. An entity whose source table cannot be
    discovered is treated as needing review (never silently skipped)."""
    results: list[dict] = []
    for a in affected:
        source_table = source_tables.get(a["source_table"])
        if source_table is None:
            results.append({
                "source_table": a["source_table"], "mappings": [],
                "needs_review": [], "auto_ok": False,
                "error": "source table schema not discoverable",
            })
            continue
        target_schema = build_target_schema(new_registry, a["target_tables"])
        results.append(remap_entity(source_table, target_schema,
                                    threshold=threshold, propose=propose, client=client))
    return results


# ---------------------------------------------------------------------------
# Orchestration (reads central + the target adapter)
# ---------------------------------------------------------------------------

def deployed_entities(conn, target_system: str = TARGET_SYSTEM) -> list[dict]:
    """Deployed Path B entities, with their target-table footprint."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, source_schema, source_table, lrmis_target_tables
            FROM integration.onboarding_entity
            WHERE target_system = %s AND status = 'deployed'
              AND lrmis_target_tables IS NOT NULL
            ORDER BY source_table
        """, (target_system,))
        return [dict(r) for r in cur.fetchall()]


def _default_fetch_entities(central, target_system):
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            return deployed_entities(conn, target_system)
    finally:
        if owns:
            central.close()


def _source_information_schema(conn, schema: str) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT table_name, column_name, data_type, is_nullable, ordinal_position
            FROM information_schema.columns
            WHERE table_schema = %s
            ORDER BY table_name, ordinal_position
        """, (schema,))
        return [dict(r) for r in cur.fetchall()]


def _default_discover_source(central, affected: list[dict]) -> dict:
    """Build `schema_models.Table` objects for the affected source tables from
    the central DB — one information_schema read per distinct source schema."""
    if not affected:
        return {}
    from ..schema_ingest import from_information_schema
    wanted: dict[str, set[str]] = {}
    for a in affected:
        wanted.setdefault(a.get("source_schema"), set()).add(a["source_table"])

    owns = central is None
    central = central or PostgresCentralConnector()
    tables: dict[str, Table] = {}
    try:
        with central.connection() as conn:
            for schema, names in wanted.items():
                model = from_information_schema(
                    _source_information_schema(conn, schema), "SOURCE")
                for name in names:
                    table = model.get_table(name)
                    if table is not None:
                        tables[name] = table
    finally:
        if owns:
            central.close()
    return tables


def _field_review_rows(mappings) -> list[dict]:
    """FieldMappings -> onboarding_field_review rows. Unmapped columns (null
    target) are skipped, so only real mappings become accepted reviews."""
    rows = []
    for m in mappings:
        if not m.target_table or not m.target_column:
            continue
        rows.append({
            "source_column": m.source_column,
            "suggested_target_table": m.target_table,
            "suggested_target_column": m.target_column,
            "confidence": m.confidence,
            "transform": m.transform or "none",
            "reasoning": getattr(m, "reasoning", None),
            "status": "accepted",
        })
    return rows


def persist_remap(conn, *, entity_id, source_fingerprint: str,
                  target_fingerprint: str, mappings, by: str) -> int:
    """Persist a re-map as a new approved proposal + accepted field reviews, so
    `load_entity_mappings` (which reads the entity's latest approved proposal)
    serves the new mapping. Returns the new proposal id."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO integration.onboarding_proposal
                (entity_id, source_fingerprint, target_fingerprint, status,
                 reviewed_by, reviewed_at, updated_at)
            VALUES (%s, %s, %s, 'approved', %s, now(), now())
            RETURNING id
        """, (entity_id, source_fingerprint, target_fingerprint, by))
        proposal_id = cur.fetchone()[0]
        for r in _field_review_rows(mappings):
            cur.execute("""
                INSERT INTO integration.onboarding_field_review
                    (proposal_id, source_column, suggested_target_table,
                     suggested_target_column, confidence, transform, reasoning, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (proposal_id, r["source_column"], r["suggested_target_table"],
                  r["suggested_target_column"], r["confidence"], r["transform"],
                  r["reasoning"], r["status"]))
    return proposal_id


def _default_persist(central, affected, remaps, new_registry, source_tables, by):
    """Persist each affected entity's re-map and deploy it against the new
    schema, so re-delivery uses the new mappings. Reuses `deploy_to_lrmis` for
    validation + target-table footprint + deployed marking."""
    from ..schema_ingest import schema_fingerprint, table_schema
    from .lrmis_onboarding import deploy_to_lrmis
    from ..mapping_engine import FieldMapping

    by_source = {r["source_table"]: r for r in remaps}
    owns = central is None
    central = central or PostgresCentralConnector()
    done = []
    try:
        with central.connection() as conn:
            for a in affected:
                remap = by_source.get(a["source_table"])
                if not remap or not remap.get("auto_ok"):
                    continue
                mappings = [FieldMapping(**m) for m in remap["mappings"]]
                src = source_tables.get(a["source_table"])
                src_schema = Schema(system_name="SOURCE", tables=[src]) if src else Schema(system_name="SOURCE", tables=[])
                tgt_schema = build_target_schema(new_registry, a["target_tables"])
                proposal_id = persist_remap(
                    conn, entity_id=a["entity_id"],
                    source_fingerprint=schema_fingerprint(src_schema),
                    target_fingerprint=schema_fingerprint(tgt_schema),
                    mappings=mappings, by=by)
                conn.commit()
                deploy_to_lrmis(proposal_id, by, central=central, registry=new_registry)
                done.append({"source_table": a["source_table"], "proposal_id": proposal_id})
    finally:
        if owns:
            central.close()
    return done


def _default_recreate(target_adapter, *, backup_path=None, dsn=None) -> dict:
    """Recreate the target from the new schema. Engine-dispatched:

    * mysql  -> the existing drop+recreate+seed of lrmis_target.
    * postgres -> restore the .backup (the archive *is* the new schema).
    """
    engine = getattr(target_adapter, "engine_type", None)
    if engine == "mysql":
        from scripts.init_lrmis_target import recreate_target_database
        return recreate_target_database()
    if engine == "postgres":
        from .pg_restore import restore_pg_backup
        return restore_pg_backup(backup_path=backup_path, dsn=dsn, dry_run=False)
    raise ValidationError(f"no recreate strategy for target engine {engine!r}")


def _default_redeliver(target_adapter, entities, *, central=None,
                       target_system=TARGET_SYSTEM, actor="cli:schema_swap",
                       registry=None) -> dict:
    """Re-deliver every deployed entity into the freshly recreated target.

    MySQL uses the legacy writer; any other engine uses the dialect-aware
    `GenericWriter` (§7) against the adapter's own connection and dialect, so a
    Postgres target now actually receives rows. Requires the adapter to expose
    `connection()`; if it does not, the pending state is reported rather than
    silently doing nothing.
    """
    from .nightly_refresh import redeliver_all
    engine = getattr(target_adapter, "engine_type", None)
    if engine == "mysql":
        return {"status": "applied",
                "entities": redeliver_all(entities, target_system=target_system)}

    if not hasattr(target_adapter, "connection"):
        return {
            "status": "pending_generic_writer",
            "note": (f"{engine!r} target adapter exposes no connection() for "
                     "delivery; rows not written."),
        }
    from ..delivery import GenericWriter
    from ..adapters.lrmis_plugin import resolve_plugin
    writer = GenericWriter(target_adapter.dialect(), registry, plugin=resolve_plugin())
    results = redeliver_all(entities, target_system=target_system,
                            target=target_adapter, writer=writer, registry=registry)
    return {"status": "applied", "entities": results}


def dry_run(*, target_adapter, target_system: str = TARGET_SYSTEM,
            central: PostgresCentralConnector | None = None,
            old_registry=None) -> dict:
    """Preview a schema swap. Changes nothing."""
    new_registry = target_adapter.discover_registry()
    old_registry = old_registry or get_registry()
    diff = diff_registries(old_registry, new_registry)

    entities = _default_fetch_entities(central, target_system)
    affected = affected_entities(diff, entities)
    return {
        "target_engine": getattr(target_adapter, "engine_type", None),
        "old_table_count": len(old_registry.table_names),
        "new_table_count": len(new_registry.table_names),
        "diff": diff,
        "deployed_entities": len(entities),
        "affected_entities": affected,
        "would_remap": [a["source_table"] for a in affected],
        "note": ("dry run — nothing changed. Re-map, recreate, and re-deliver "
                 "run only on a confirmed apply."),
    }


def apply(*, target_adapter, actor: str = "cli:schema_swap",
          target_system: str = TARGET_SYSTEM, threshold: float = 0.7,
          force: bool = False, central: PostgresCentralConnector | None = None,
          backup_path: str | None = None, dsn: str | None = None,
          old_registry=None, propose=None, client=None,
          fetch_entities=None, discover_source=None, persist=None,
          recreate=None, redeliver=None) -> dict:
    """Confirmed, destructive schema swap.

    Discovers the new target, diffs it, re-maps the affected entities with the
    AI, and — only if no re-mapping needs review (or `force=True`) — recreates
    the target and re-delivers the kept entities. If the gate blocks, NOTHING is
    changed. The seams (`fetch_entities`/`discover_source`/`recreate`/
    `redeliver`/`propose`) are injectable for testing; defaults hit the live
    systems. Callers must confirm intent before invoking (the CLI enforces a
    typed target-name check, mirroring the nightly rebuild).
    """
    new_registry = target_adapter.discover_registry()
    old_registry = old_registry or get_registry()
    diff = diff_registries(old_registry, new_registry)

    entities = (fetch_entities or _default_fetch_entities)(central, target_system)
    affected = affected_entities(diff, entities)
    source_tables = (discover_source or _default_discover_source)(central, affected)
    remaps = remap_affected(affected, new_registry, source_tables,
                            threshold=threshold, propose=propose, client=client)

    result = {
        "target_engine": getattr(target_adapter, "engine_type", None),
        "diff": diff,
        "affected_entities": [a["source_table"] for a in affected],
        "deployed_entities": [e["source_table"] for e in entities],
        "remaps": remaps,
    }

    blocked = [r for r in remaps if not r.get("auto_ok")]
    if blocked and not force:
        result["status"] = "blocked_on_review"
        result["blocked"] = [r["source_table"] for r in blocked]
        result["note"] = ("low-confidence re-mappings need review; nothing was "
                          "changed. Resolve them or re-run with force=True.")
        return result

    # --- destructive from here ---
    # Persist the new mappings first, so re-delivery loads them (not the old ones).
    result["persisted"] = (persist or _default_persist)(
        central, affected, remaps, new_registry, source_tables, actor)
    result["recreate"] = (recreate or _default_recreate)(
        target_adapter, backup_path=backup_path, dsn=dsn)
    result["redeliver"] = (redeliver or _default_redeliver)(
        target_adapter, entities, central=central,
        target_system=target_system, actor=actor, registry=new_registry)
    result["status"] = result["redeliver"].get("status", "applied")
    return result
