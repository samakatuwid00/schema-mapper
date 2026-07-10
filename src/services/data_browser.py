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
TARGET = "target"
SIDES = (SOURCE, TARGET)

DEFAULT_PAGE_SIZE = 25


def _source_schema() -> str:
    return os.environ.get("SOURCE_SCHEMA", "irimsv")


def _staging_database() -> str:
    return os.environ.get("LRMIS_STAGING_DATABASE", "lrmis_staging")


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


def _columns_for(side: str, central, staging, schema: str) -> dict[str, list[dict]]:
    if side == SOURCE:
        return _source_columns(central, schema)
    if side == TARGET:
        return _target_columns(staging)
    raise ValidationError(f"side must be one of {SIDES}, got {side!r}")


def _resolve(side: str, table: str, sort: str | None,
             central, staging, schema: str) -> list[dict]:
    """Allowlist the table and sort column; return the table's column metadata."""
    if side not in SIDES:
        raise ValidationError(f"side must be one of {SIDES}, got {side!r}")
    tables = _columns_for(side, central, staging, schema)
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
                          source_schema: str | None = None) -> dict:
    """Both sides' tables with column and row counts, plus the entity link."""
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    staging = staging or MySQLStagingConnector()
    schema = source_schema or _source_schema()
    try:
        with central.connection() as conn:
            entities = p._query(conn, """
                SELECT source_table, staging_table, status, target_system
                FROM integration.onboarding_entity
            """)
        by_source = {e["source_table"]: e for e in entities}
        by_staging = {e["staging_table"]: e for e in entities if e["staging_table"]}

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

        return {
            "source": {"schema": schema, "tables": source_tables},
            "target": {"database": _staging_database(), "tables": target_tables},
        }
    finally:
        if owns:
            central.close()


def fetch_rows(side: str, table: str, page: int = 1, size: int = DEFAULT_PAGE_SIZE,
               sort: str | None = None, direction: str = "asc",
               central: PostgresCentralConnector | None = None,
               staging: MySQLStagingConnector | None = None,
               source_schema: str | None = None) -> dict:
    """One page of rows. Size is clamped, never rejected, so a UI cannot wedge."""
    owns = central is None
    central = central or PostgresCentralConnector()
    staging = staging or MySQLStagingConnector()
    schema = source_schema or _source_schema()
    try:
        columns = _resolve(side, table, sort, central, staging, schema)
        if direction.lower() not in ("asc", "desc"):
            raise ValidationError(f"direction must be asc or desc, got {direction!r}")
        size = max(1, min(int(size), MAX_PAGE_SIZE))
        page = max(1, int(page))
        offset = (page - 1) * size

        if side == SOURCE:
            total = central.count_rows(schema, table)
            rows = central.fetch_rows(schema, table, size, offset, sort, direction)
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
