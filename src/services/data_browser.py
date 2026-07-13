"""Read-only row access to the source and target databases (data-browser spec).

Security posture, in order of defence:
  1. Session auth + audit at the API layer (see admin_api.routers).
  2. Allowlisting here: the requested table must appear in the live
     information_schema listing for its side, and the requested sort column
     must be one of that table's columns.
  3. `connectors.safe_identifier` as a last-resort identifier check.
  4. SELECT-only connector helpers; no user SQL ever reaches the database.

Nothing in this module writes. Page size is capped at MAX_PAGE_SIZE.
"""
from __future__ import annotations

import os

from ..connectors import MAX_PAGE_SIZE, MySQLStagingConnector, PostgresCentralConnector
from .common import NotFoundError, ValidationError

SOURCE = "source"
TARGET = "target"          # Path A: lrmis_staging
PATH_B = "path_b"          # Path B: lrmis_target (canonical LRMIS tables)
SIDES = (SOURCE, TARGET, PATH_B)

DEFAULT_PAGE_SIZE = 25


def _source_schema() -> str:
    return os.environ.get("SOURCE_SCHEMA", "irimsv")


def _staging_database() -> str:
    return os.environ.get("LRMIS_STAGING_DATABASE", "lrmis_staging")


def _target_database() -> str:
    return os.environ.get("LRMIS_TARGET_DATABASE", "lrmis_target")


def _canonical_name(staging_table: str) -> str:
    """Reduce a staging table name to its canonical LRMIS table name.

    ``irimsv_parcels_staging`` -> ``parcels``; ``staging_parcels`` -> ``parcels``.
    Used to line a Path A staging table up with its Path B canonical table.
    """
    name = staging_table
    if name.startswith("irimsv_"):
        name = name[len("irimsv_"):]
    if name.endswith("_staging"):
        name = name[: -len("_staging")]
    if name.startswith("staging_"):
        name = name[len("staging_"):]
    return name


def _match_staging_table(path_b_table: str, staging_names: set[str],
                         by_canonical: dict[str, str]) -> str | None:
    """The Path A staging table that corresponds to a Path B canonical table.

    Priority: an entity whose staging table normalises to this canonical name
    (authoritative), then the ``staging_`` / ``irimsv_*_staging`` prefix fallback.
    """
    match = by_canonical.get(path_b_table)
    if match:
        return match
    for candidate in (f"staging_{path_b_table}", f"irimsv_{path_b_table}_staging"):
        if candidate in staging_names:
            return candidate
    return None


def _pipeline():
    from .. import pipeline
    return pipeline


# ---------------------------------------------------------------------------
# Allowlist construction (from live information_schema, never from user input)
# ---------------------------------------------------------------------------

def _source_columns(central: PostgresCentralConnector, schema: str) -> dict[str, list[dict]]:
    """table -> [{name, data_type, nullable, is_primary_key}] for the source schema."""
    p = _pipeline()
    with central.connection() as conn:
        discovered = p._discover_source_schema(conn, schema)
    return {
        t.name: [
            {"name": c.name, "data_type": c.data_type, "nullable": c.nullable,
             "is_primary_key": c.is_primary_key}
            for c in t.columns
        ]
        for t in discovered.tables
    }


def _target_columns(staging: MySQLStagingConnector) -> dict[str, list[dict]]:
    """table -> [{name, data_type, nullable, is_primary_key}] for the staging database."""
    grouped: dict[str, list[dict]] = {}
    for row in staging.information_schema():
        r = {k.lower(): v for k, v in row.items()}
        grouped.setdefault(r["table_name"], []).append({
            "name": r["column_name"],
            "data_type": r["data_type"],
            "nullable": r["is_nullable"] == "YES",
            "is_primary_key": r.get("column_key") == "PRI",
        })
    return grouped


def _columns_for(side: str, central, staging, schema: str, target=None) -> dict[str, list[dict]]:
    if side == SOURCE:
        return _source_columns(central, schema)
    if side == TARGET:
        return _target_columns(staging)
    if side == PATH_B:
        # Path B reuses the staging column reader against the lrmis_target DB.
        return _target_columns(target or MySQLStagingConnector.for_target())
    raise ValidationError(f"side must be one of {SIDES}, got {side!r}")


def _resolve(side: str, table: str, sort: str | None,
             central, staging, schema: str, target=None) -> list[dict]:
    """Allowlist the table and sort column; return the table's column metadata."""
    if side not in SIDES:
        raise ValidationError(f"side must be one of {SIDES}, got {side!r}")
    tables = _columns_for(side, central, staging, schema, target)
    if table not in tables:
        raise NotFoundError(f"table {table!r} is not browsable on the {side} side")
    columns = tables[table]
    if sort and sort not in {c["name"] for c in columns}:
        raise ValidationError(f"sort column {sort!r} is not a column of {table!r}")
    return columns


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_browsable_tables(central: PostgresCentralConnector | None = None,
                          staging: MySQLStagingConnector | None = None,
                          source_schema: str | None = None,
                          target: MySQLStagingConnector | None = None) -> dict:
    """All three sides' tables with column and row counts, plus the entity link.

    ``source`` = IRIMSV, ``target`` = Path A ``lrmis_staging``, ``path_b`` =
    Path B ``lrmis_target``. Each Path B table is annotated with the staging
    table it corresponds to, so the UI can offer a staging<->target comparison.
    """
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    staging = staging or MySQLStagingConnector()
    target = target or MySQLStagingConnector.for_target()
    schema = source_schema or _source_schema()
    try:
        with central.connection() as conn:
            entities = p._query(conn, """
                SELECT source_table, staging_table, status, target_system
                FROM integration.onboarding_entity
            """)
        by_source = {e["source_table"]: e for e in entities}
        by_staging = {e["staging_table"]: e for e in entities if e["staging_table"]}
        staging_names = set(by_staging)
        by_canonical = {_canonical_name(st): st for st in staging_names}

        source_tables = []
        for name, cols in sorted(_source_columns(central, schema).items()):
            entity = by_source.get(name)
            source_tables.append({
                "table": name,
                "columns": len(cols),
                "rows": central.count_rows(schema, name),
                "entity_status": entity["status"] if entity else None,
                "staging_table": entity["staging_table"] if entity else None,
            })

        target_tables = []
        for name, cols in sorted(_target_columns(staging).items()):
            entity = by_staging.get(name)
            target_tables.append({
                "table": name,
                "columns": len(cols),
                "rows": staging.count_rows(name),
                "entity_status": entity["status"] if entity else None,
                "source_table": entity["source_table"] if entity else None,
            })

        path_b_tables = []
        for name, cols in sorted(_target_columns(target).items()):
            staging_table = _match_staging_table(name, staging_names, by_canonical)
            path_b_tables.append({
                "table": name,
                "columns": len(cols),
                "rows": target.count_rows(name),
                # The Path A staging table this canonical table can be compared to.
                "staging_table": staging_table,
            })

        return {
            "source": {"schema": schema, "tables": source_tables},
            "target": {"database": _staging_database(), "tables": target_tables},
            "path_b": {"database": _target_database(), "tables": path_b_tables},
        }
    finally:
        if owns:
            central.close()


def fetch_rows(side: str, table: str, page: int = 1, size: int = DEFAULT_PAGE_SIZE,
               sort: str | None = None, direction: str = "asc",
               central: PostgresCentralConnector | None = None,
               staging: MySQLStagingConnector | None = None,
               source_schema: str | None = None,
               target: MySQLStagingConnector | None = None) -> dict:
    """One page of rows. Size is clamped, never rejected, so a UI cannot wedge."""
    owns = central is None
    central = central or PostgresCentralConnector()
    staging = staging or MySQLStagingConnector()
    schema = source_schema or _source_schema()
    if side == PATH_B and target is None:
        target = MySQLStagingConnector.for_target()
    try:
        columns = _resolve(side, table, sort, central, staging, schema, target)
        if direction.lower() not in ("asc", "desc"):
            raise ValidationError(f"direction must be asc or desc, got {direction!r}")
        size = max(1, min(int(size), MAX_PAGE_SIZE))
        page = max(1, int(page))
        offset = (page - 1) * size

        if side == SOURCE:
            total = central.count_rows(schema, table)
            rows = central.fetch_rows(schema, table, size, offset, sort, direction)
        elif side == PATH_B:
            total = target.count_rows(table)
            rows = target.fetch_rows(table, size, offset, sort, direction)
        else:
            total = staging.count_rows(table)
            rows = staging.fetch_rows(table, size, offset, sort, direction)

        return {
            "side": side,
            "table": table,
            "columns": columns,
            "rows": rows,
            "total": total,
            "page": page,
            "size": size,
            "pages": max(1, (total + size - 1) // size),
        }
    finally:
        if owns:
            central.close()


def compare_row(entity: str, external_reference: str,
                central: PostgresCentralConnector | None = None,
                staging: MySQLStagingConnector | None = None,
                source_schema: str | None = None) -> dict:
    """Match one logical record across both sides on its external_reference UUID.

    The source table has no external_reference column, so the source row is
    located through the outbox event that carries the payload for that UUID.
    """
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    staging = staging or MySQLStagingConnector()
    schema = source_schema or _source_schema()
    try:
        with central.connection() as conn:
            entities = p._query(conn, """
                SELECT * FROM integration.onboarding_entity WHERE source_table = %s
            """, (entity,))
            if not entities:
                raise NotFoundError(f"entity {entity!r} not found")
            row = entities[0]
            staging_table = row["staging_table"]

            events = p._query(conn, """
                SELECT payload, status, operation, created_at, processed_at
                FROM integration.outbox
                WHERE source_entity = %s AND external_reference = %s
                ORDER BY created_at DESC LIMIT 1
            """, (entity, str(external_reference)))

        source_row = events[0]["payload"] if events else None
        delivery_status = events[0]["status"] if events else None

        target_row = None
        if staging_table:
            target_row = staging.fetch_row_by(staging_table, "external_reference",
                                              str(external_reference))

        fields = []
        if source_row and target_row:
            for key in sorted(set(source_row) | set(target_row)):
                src, tgt = source_row.get(key), target_row.get(key)
                present = key in source_row and key in target_row
                fields.append({
                    "field": key,
                    "source": src,
                    "target": tgt,
                    # Only compare fields carried on both sides; the target adds
                    # envelope columns the source never had.
                    "matches": present and str(src) == str(tgt),
                    "compared": present,
                })

        return {
            "entity": entity,
            "external_reference": str(external_reference),
            "staging_table": staging_table,
            "delivery_status": delivery_status,
            "source_row": source_row,
            "target_row": target_row,
            "missing_in_target": target_row is None,
            "missing_in_source": source_row is None,
            "fields": fields,
        }
    finally:
        if owns:
            central.close()


def compare_source_target(entity: str, primary_key_value,
                          target_system: str = "LRMIS",
                          central: PostgresCentralConnector | None = None,
                          target: MySQLStagingConnector | None = None) -> dict:
    """Match one source row to the exact rows it produced in the LRMIS target.

    Direct source -> target (Path B) check, keyed on the source row's primary
    key: derive the same deterministic external_reference the pipeline uses, find
    every target row the writer recorded in the crosswalk, and compare per mapping
    — `source_col -> table.column`, source value (after transform) vs the delivered
    target value. A stale legacy-staging crosswalk row is ignored.
    """
    import json as _json
    from ..lrmis_delivery import load_entity_mappings
    from ..lrmis_registry import get_registry
    from ..transform_engine import _apply_transform
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    if target is None:
        target = MySQLStagingConnector.for_target()
    registry = get_registry()
    try:
        with central.connection() as conn:
            ent = p._query(conn, """
                SELECT source_schema, primary_key_columns, source_system
                FROM integration.onboarding_entity
                WHERE source_table = %s AND target_system = %s
                  AND lrmis_target_tables IS NOT NULL
                ORDER BY updated_at DESC LIMIT 1
            """, (entity, target_system))
            if not ent:
                raise NotFoundError(f"{entity!r} is not deployed to the LRMIS target")
            source_schema = ent[0]["source_schema"]
            pk_cols = ent[0]["primary_key_columns"]
            if isinstance(pk_cols, str):
                pk_cols = _json.loads(pk_cols)
            pk_cols = pk_cols or ["id"]

            source_rows = p._query(
                conn, f'SELECT * FROM {source_schema}.{entity} WHERE {pk_cols[0]} = %s LIMIT 1',
                (primary_key_value,))
            source_row = source_rows[0] if source_rows else None

            external_reference, crosswalk = None, []
            if source_row:
                pk_values = [source_row.get(c) for c in pk_cols]
                external_reference = str(p.generate_external_reference(
                    ent[0]["source_system"] or "IRIMSV_REGION_V", source_schema, entity, pk_values))
                crosswalk = p._query(conn, """
                    SELECT target_table, target_id FROM integration.id_crosswalk
                    WHERE source_entity = %s AND external_reference = %s AND target_system = %s
                """, (entity, external_reference, target_system))
            mappings = load_entity_mappings(conn, entity, target_system)

        # Only real LRMIS tables — a stale legacy-staging crosswalk row is ignored.
        target_ids = {r["target_table"]: r["target_id"]
                      for r in crosswalk if registry.has_table(r["target_table"])}
        target_rows: dict[str, dict] = {}
        for tbl, tid in target_ids.items():
            pk = registry.get_table(tbl).primary_key
            if not pk:
                continue
            row = target.fetch_row_by(tbl, pk[0], tid)
            if row:
                target_rows[tbl] = row

        fields = []
        for m in mappings:
            src_col, tbl, tgt_col = m["source_column"], m["target_table"], m["target_column"]
            src_val = (source_row or {}).get(src_col)
            try:
                transformed = _apply_transform(src_val, m.get("transform", "none"))
            except Exception:
                transformed = src_val
            trow = target_rows.get(tbl)
            tgt_val = trow.get(tgt_col) if trow else None
            compared = trow is not None
            fields.append({
                "field": f"{src_col} → {tbl}.{tgt_col}",
                "source": src_val,
                "target": tgt_val,
                "matches": compared and str(transformed) == str(tgt_val),
                "compared": compared,
            })

        return {
            "entity": entity,
            "primary_key": pk_cols[0],
            "primary_key_value": str(primary_key_value),
            "external_reference": external_reference,
            "target_tables": sorted(target_ids),
            "missing_in_target": bool(source_row) and not target_ids,
            "missing_in_source": source_row is None,
            "fields": fields,
        }
    finally:
        if owns:
            central.close()


def compare_staging_target(staging_table: str, primary_key_value,
                           staging: MySQLStagingConnector | None = None,
                           target: MySQLStagingConnector | None = None,
                           central: PostgresCentralConnector | None = None) -> dict:
    """Compare one Path A staging row against its Path B canonical row by PK.

    Both databases share the LRMIS column layout, so the two rows are matched on
    the canonical primary key and compared field by field. ``central`` is
    accepted for router symmetry but never opened — this is a pure MySQL read.
    """
    staging = staging or MySQLStagingConnector()
    target = target or MySQLStagingConnector.for_target()

    staging_cols = _target_columns(staging)
    if staging_table not in staging_cols:
        raise NotFoundError(f"staging table {staging_table!r} is not browsable")

    path_b_table = _canonical_name(staging_table)
    target_cols = _target_columns(target)
    if path_b_table not in target_cols:
        raise NotFoundError(
            f"no Path B (lrmis_target) table matches staging table "
            f"{staging_table!r} (looked for {path_b_table!r})")

    columns = target_cols[path_b_table]
    pk_col = next((c["name"] for c in columns if c["is_primary_key"]), None)
    if pk_col is None:
        pk_col = "id" if any(c["name"] == "id" for c in columns) else columns[0]["name"]

    staging_names = {c["name"] for c in staging_cols[staging_table]}
    staging_row = (staging.fetch_row_by(staging_table, pk_col, primary_key_value)
                   if pk_col in staging_names else None)
    target_row = target.fetch_row_by(path_b_table, pk_col, primary_key_value)

    fields = []
    if staging_row and target_row:
        for key in sorted(set(staging_row) | set(target_row)):
            s_val, t_val = staging_row.get(key), target_row.get(key)
            present = key in staging_row and key in target_row
            fields.append({
                "field": key,
                "staging": s_val,
                "target": t_val,
                "matches": present and str(s_val) == str(t_val),
                "compared": present,
            })

    return {
        "staging_table": staging_table,
        "path_b_table": path_b_table,
        "primary_key": pk_col,
        "primary_key_value": primary_key_value,
        "staging_row": staging_row,
        "target_row": target_row,
        "missing_in_target": target_row is None,
        "missing_in_staging": staging_row is None,
        "fields": fields,
    }
