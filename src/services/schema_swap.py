"""Schema-swap dry-run + confirmed apply (generic engine, §0.4–0.5).

When the schema on either side of the pipeline changes, this computes what a
swap would do — discover the new structure, diff it against the approved
contract for that side, and list only the deployed Path B entities the change
affects — and then, on a confirmed apply, re-maps those entities with the AI
(schema-only, human-gated on low confidence). The pipeline is side-agnostic
(``side="target"`` by default, preserving every existing call site):

* ``side="target"`` — discover through the target adapter, diff against the
  approved registry (``get_registry()``); an entity is affected when its
  ``onboarding_entity.lrmis_target_tables`` footprint intersects the changed
  tables. The confirmed apply is destructive: persist re-maps, recreate the
  target, re-deliver the kept entities.
* ``side="source"`` — discover through the source adapter
  (``PostgresSourceAdapter``), diff each deployed entity's live source table
  against its approved source contract. That contract is, per entity: the
  accepted ``onboarding_field_review.source_column`` set of its latest
  approved proposal (always available; detects added/removed columns), plus —
  once captured — a full ``integration.schema_version`` document with
  ``scope_kind='entity_source'`` (adapter-normalized construction; adds
  retype/nullability detection). Contracts are captured on every confirmed
  source-swap apply. The entity-level hash ``onboarding_entity
  .source_fingerprint`` (pipeline-discovery construction — raw types +
  descriptions, NOT comparable to the adapter construction) remains
  ``ops.monitor``'s drift contract and is refreshed on apply.

A source-swap never issues DDL or DML against the source (design D2): its
apply writes central metadata only (new approved proposals, refreshed
contracts) and has no recreate/redeliver step — delivery resumes for
auto-approved entities via the ordinary deploy path.

Selective delivery is preserved throughout: entities not onboarded to the
target are never in scope. The dry-run is read-only; the low-confidence gate
blocks before anything is changed.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Literal

import psycopg2.extras

from ..connectors import PostgresCentralConnector
from ..lrmis_registry import get_registry
from ..schema_models import Column, Schema, Table
from .common import ValidationError

TARGET_SYSTEM = "LRMIS"

# integration.schema_version scope for per-entity approved source contracts
# (sql/012_entity_source_contract.sql adds it to the scope_kind check).
SOURCE_SCOPE_KIND = "entity_source"


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
# Source-side diff (per entity, against the approved source contract)
# ---------------------------------------------------------------------------

def diff_source_table(old_table: Table | None, new_table: Table | None) -> dict:
    """Column-level diff for one entity's source table — the same comparison
    `diff_registries` applies per table (presence + type/nullability)."""
    if new_table is None:
        return {"missing_table": True, "added_columns": [],
                "removed_columns": sorted(c.name for c in old_table.columns) if old_table else [],
                "changed_columns": []}
    if old_table is None:
        return {"missing_table": False,
                "added_columns": sorted(c.name for c in new_table.columns),
                "removed_columns": [], "changed_columns": []}
    oc = {c.name: c for c in old_table.columns}
    nc = {c.name: c for c in new_table.columns}
    return {
        "missing_table": False,
        "added_columns": sorted(set(nc) - set(oc)),
        "removed_columns": sorted(set(oc) - set(nc)),
        "changed_columns": sorted(
            c for c in (set(oc) & set(nc))
            if (oc[c].data_type, oc[c].nullable) != (nc[c].data_type, nc[c].nullable)),
    }


def affected_source_entities(new_schema: Schema, entities: list[dict],
                             contracts: dict) -> tuple[list[dict], dict]:
    """Diff every deployed entity's source table against its approved contract.

    ``contracts`` maps source_table -> {"table": Table | None (captured
    entity_source document), "columns": [approved source column names] | None}.
    An entity is affected when its table is missing, an approved column was
    removed, or a column was retyped. Added-only changes never invalidate the
    approved mapping, so they are reported but trigger no remap (the spec's
    "unaffected entities keep delivering uninterrupted").
    """
    details: dict[str, dict] = {}
    affected: list[dict] = []
    for entity in entities:
        name = entity["source_table"]
        new_table = new_schema.get_table(name)
        contract = contracts.get(name) or {}
        old_table = contract.get("table")
        contract_source = "document" if old_table is not None else "approved_columns"
        if old_table is None and new_table is not None:
            # Names-only contract: mirror live types so only presence changes
            # register (retype detection needs a captured document).
            synthesized = []
            for col in (contract.get("columns") or []):
                live = new_table.get_column(col)
                synthesized.append(Column(
                    name=col,
                    data_type=live.data_type if live else "unknown",
                    nullable=live.nullable if live else True))
            old_table = Table(name=name, columns=synthesized)
        diff = diff_source_table(old_table, new_table)
        diff["contract_source"] = contract_source
        details[name] = diff
        if diff["missing_table"] or diff["removed_columns"] or diff["changed_columns"]:
            affected.append({
                "entity_id": entity.get("id"),
                "source_schema": entity.get("source_schema"),
                "source_table": name,
                "target_tables": _entity_target_tables(entity),
                "affected_tables": [name],
                "source_diff": diff,
            })
    return affected, details


def _approved_source_columns(conn, target_system: str = TARGET_SYSTEM) -> dict[str, list[str]]:
    """source_table -> accepted source columns of its latest approved proposal
    (the always-available half of the approved source contract)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT e.source_table, r.source_column
            FROM integration.onboarding_entity e
            JOIN LATERAL (
                SELECT p.id FROM integration.onboarding_proposal p
                WHERE p.entity_id = e.id
                  AND p.status IN ('approved', 'auto_approved')
                ORDER BY p.created_at DESC LIMIT 1
            ) latest ON true
            JOIN integration.onboarding_field_review r ON r.proposal_id = latest.id
            WHERE e.target_system = %s AND e.status = 'deployed'
              AND e.lrmis_target_tables IS NOT NULL
              AND r.status IN ('accepted', 'resolved')
        """, (target_system,))
        out: dict[str, list[str]] = {}
        for row in cur.fetchall():
            out.setdefault(row["source_table"], []).append(row["source_column"])
    return out


def _stored_source_contracts(conn, target_system: str = TARGET_SYSTEM) -> dict[str, Table]:
    """source_table -> Table from the latest captured entity_source document."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT DISTINCT ON (scope_name) scope_name, schema_document
            FROM integration.schema_version
            WHERE target_system = %s AND scope_kind = %s
            ORDER BY scope_name, observed_at DESC
        """, (target_system, SOURCE_SCOPE_KIND))
        out: dict[str, Table] = {}
        for row in cur.fetchall():
            doc = Schema.from_dict(row["schema_document"])
            table = doc.get_table(row["scope_name"]) or (doc.tables[0] if doc.tables else None)
            if table is not None:
                out[row["scope_name"]] = table
    return out


def _default_fetch_source_contracts(central, target_system: str) -> dict:
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            stored = _stored_source_contracts(conn, target_system)
            columns = _approved_source_columns(conn, target_system)
    finally:
        if owns:
            central.close()
    return {name: {"table": stored.get(name), "columns": columns.get(name)}
            for name in set(stored) | set(columns)}


def _store_source_contract(conn, entity: dict, table: Table, by: str,
                           target_system: str = TARGET_SYSTEM) -> None:
    """Capture the entity's new source contract document (adapter-normalized
    construction) so future source-swaps can detect retypes exactly."""
    from ..schema_ingest import schema_fingerprint
    doc = Schema(system_name=str(entity.get("source_schema") or "SOURCE").upper(),
                 tables=[table])
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO integration.schema_version
                (target_system, scope_kind, scope_name, fingerprint,
                 schema_document, approved_at, approved_by)
            VALUES (%s, %s, %s, %s, %s, now(), %s)
            ON CONFLICT (target_system, scope_kind, scope_name, fingerprint)
            DO UPDATE SET schema_document = EXCLUDED.schema_document, approved_at = now()
        """, (target_system, SOURCE_SCOPE_KIND, entity["source_table"],
              schema_fingerprint(doc), json.dumps(doc.to_dict()), by))


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


def _default_persist_source(central, affected, remaps, target_registry,
                            source_tables, by, target_system=TARGET_SYSTEM):
    """Persist source-side re-maps: new approved proposal + deploy (which
    re-enables delivery), then refresh each entity's approved source contract —
    the captured entity_source document (adapter construction, for exact future
    diffs) and ``onboarding_entity.source_fingerprint`` (pipeline construction,
    what ``ops.monitor`` compares). Central metadata only — the source database
    is never written (design D2).

    The fingerprint refresh discovers through the configured central
    connection, which is correct for an in-place restructure. After swapping to
    a replacement source database, re-point the source configuration and run
    ``rebaseline_entity_fingerprints --apply``.
    """
    done = _default_persist(central, affected, remaps, target_registry,
                            source_tables, by)
    persisted = {d["source_table"] for d in done}
    if not persisted:
        return done

    from ..pipeline import _discover_source_schema
    from ..schema_ingest import schema_fingerprint, table_schema

    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            discovered: dict[str, Schema] = {}
            for a in affected:
                if a["source_table"] not in persisted:
                    continue
                table = source_tables.get(a["source_table"])
                if table is not None:
                    _store_source_contract(conn, a, table, by, target_system)
                schema_name = a.get("source_schema")
                if schema_name and schema_name not in discovered:
                    discovered[schema_name] = _discover_source_schema(conn, schema_name)
                contract = (table_schema(discovered[schema_name], a["source_table"])
                            if schema_name in discovered else None)
                if contract is not None:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE integration.onboarding_entity
                            SET source_fingerprint = %s,
                                fingerprint_scope_version = 2, updated_at = now()
                            WHERE id = %s
                        """, (schema_fingerprint(contract), a["entity_id"]))
            conn.commit()
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


def dry_run(*, target_adapter=None, side: Literal["source", "target"] = "target",
            source_adapter=None, target_system: str = TARGET_SYSTEM,
            central: PostgresCentralConnector | None = None,
            old_registry=None, fetch_entities=None,
            fetch_source_contracts=None) -> dict:
    """Preview a schema swap on either side. Changes nothing."""
    if side == "source":
        if source_adapter is None:
            raise ValidationError("side='source' requires a source_adapter")
        new_schema = source_adapter.discover_schema()
        entities = (fetch_entities or _default_fetch_entities)(central, target_system)
        contracts = (fetch_source_contracts or _default_fetch_source_contracts)(
            central, target_system)
        affected, details = affected_source_entities(new_schema, entities, contracts)
        return {
            "side": "source",
            "source_engine": getattr(source_adapter, "engine_type", None),
            "source_schema": getattr(source_adapter, "schema", None),
            "new_table_count": len(new_schema.table_names),
            "diff": details,
            "deployed_entities": len(entities),
            "affected_entities": affected,
            "would_remap": [a["source_table"] for a in affected],
            "note": ("dry run — nothing changed, and a source-swap never issues "
                     "DDL/DML against the source. Entities without a captured "
                     "entity_source contract are diffed against their approved "
                     "mapping's source columns (adds/removes only; retype "
                     "detection needs a captured contract document)."),
        }

    if target_adapter is None:
        raise ValidationError("side='target' requires a target_adapter")
    new_registry = target_adapter.discover_registry()
    old_registry = old_registry or get_registry()
    diff = diff_registries(old_registry, new_registry)

    entities = (fetch_entities or _default_fetch_entities)(central, target_system)
    affected = affected_entities(diff, entities)
    return {
        "side": "target",
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


def apply(*, target_adapter=None, side: Literal["source", "target"] = "target",
          source_adapter=None, actor: str = "cli:schema_swap",
          target_system: str = TARGET_SYSTEM, threshold: float = 0.7,
          force: bool = False, central: PostgresCentralConnector | None = None,
          backup_path: str | None = None, dsn: str | None = None,
          old_registry=None, propose=None, client=None,
          fetch_entities=None, fetch_source_contracts=None,
          discover_source=None, persist=None,
          recreate=None, redeliver=None) -> dict:
    """Confirmed schema swap (destructive on the target side only).

    ``side="target"`` (default) discovers the new target, diffs it, re-maps the
    affected entities with the AI, and — only if no re-mapping needs review (or
    `force=True`) — recreates the target and re-delivers the kept entities.
    ``side="source"`` discovers the new source, diffs each deployed entity
    against its approved source contract, re-maps the affected ones (same gate),
    and persists the approved re-maps so delivery resumes — with no recreate or
    redeliver step and no DDL/DML against the source (design D2). If the gate
    blocks, NOTHING is changed on either side. The seams (`fetch_entities`/
    `fetch_source_contracts`/`discover_source`/`recreate`/`redeliver`/`propose`)
    are injectable for testing; defaults hit the live systems. Callers must
    confirm intent before invoking (the CLI enforces a typed check).
    """
    if side == "source":
        if source_adapter is None:
            raise ValidationError("side='source' requires a source_adapter")
        return _apply_source(
            source_adapter=source_adapter, actor=actor,
            target_system=target_system, threshold=threshold, force=force,
            central=central, old_registry=old_registry, propose=propose,
            client=client, fetch_entities=fetch_entities,
            fetch_source_contracts=fetch_source_contracts, persist=persist)

    if target_adapter is None:
        raise ValidationError("side='target' requires a target_adapter")
    new_registry = target_adapter.discover_registry()
    old_registry = old_registry or get_registry()
    diff = diff_registries(old_registry, new_registry)

    entities = (fetch_entities or _default_fetch_entities)(central, target_system)
    affected = affected_entities(diff, entities)
    source_tables = (discover_source or _default_discover_source)(central, affected)
    remaps = remap_affected(affected, new_registry, source_tables,
                            threshold=threshold, propose=propose, client=client)

    result = {
        "side": "target",
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


def _apply_source(*, source_adapter, actor, target_system, threshold, force,
                  central, old_registry, propose, client,
                  fetch_entities, fetch_source_contracts, persist) -> dict:
    """Confirmed source-side swap: re-discover, diff against the approved
    source contracts, re-map the affected entities (human-gated exactly like
    the target side), and persist the approved re-maps so delivery resumes.
    Reads the source, writes only central metadata — never the source (D2)."""
    new_schema = source_adapter.discover_schema()
    target_registry = old_registry or get_registry()

    entities = (fetch_entities or _default_fetch_entities)(central, target_system)
    contracts = (fetch_source_contracts or _default_fetch_source_contracts)(
        central, target_system)
    affected, details = affected_source_entities(new_schema, entities, contracts)
    source_tables = {a["source_table"]: t for a in affected
                     if (t := new_schema.get_table(a["source_table"])) is not None}
    remaps = remap_affected(affected, target_registry, source_tables,
                            threshold=threshold, propose=propose, client=client)

    result = {
        "side": "source",
        "source_engine": getattr(source_adapter, "engine_type", None),
        "diff": details,
        "affected_entities": [a["source_table"] for a in affected],
        "deployed_entities": [e["source_table"] for e in entities],
        "remaps": remaps,
    }

    blocked = [r for r in remaps if not r.get("auto_ok")]
    if blocked and not force:
        result["status"] = "blocked_on_review"
        result["blocked"] = [r["source_table"] for r in blocked]
        result["note"] = ("low-confidence re-mappings need review; nothing was "
                          "changed and delivery stays paused for those "
                          "entities. Resolve them or re-run with force=True.")
        return result

    persist_fn = persist or (lambda *a: _default_persist_source(
        *a, target_system=target_system))
    result["persisted"] = persist_fn(
        central, affected, remaps, target_registry, source_tables, actor)
    result["status"] = "applied"
    result["note"] = ("source swap applied: re-maps approved and deployed, "
                      "delivery resumed for auto-ok entities. The source "
                      "database was not modified.")
    return result
