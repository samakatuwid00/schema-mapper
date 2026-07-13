"""
Fast refresh module for dropping and recreating staging tables.
Bypasses the outbox pattern for maximum performance.

Uses INSERT INTO ... SELECT for PostgreSQL bulk operations
and executemany for MySQL bulk inserts.
"""
from __future__ import annotations

import json
import hashlib
import uuid
import datetime
import logging

from .connectors import VIEWS_DATABASE

log = logging.getLogger(__name__)

UUID5_NAMESPACE = uuid.UUID("12345678-1234-5678-1234-567812345678")


def _qt(table: str) -> str:
    return f"`{VIEWS_DATABASE}`.`{table}`" if "_for_lrmis" in table else f"`{table}`"


def generate_refresh_sql(
    source_schema: str,
    source_table: str,
    target_table: str,
    mappings: list[dict],
    source_system: str,
    pk_columns: list[str] | None = None,
    updated_at_column: str | None = None,
) -> str:
    """Generate PostgreSQL SELECT statement for refresh."""
    pk_cols = pk_columns or []
    if pk_cols:
        pk_concat = "||'|'|| ".join([f"s.{pk}" for pk in pk_cols])
        ref_key = (f"'{source_system}|{source_schema}|{source_table}|' "
                   f"|| {pk_concat}::text")
    else:
        # Source table has no usable primary key: identify each row by a hash of
        # its full contents. This references no named column, so it cannot fail
        # with "column s.id does not exist" on id-less tables. Byte-identical
        # rows map to the same external_reference, which is the correct dedup
        # behaviour for a table with no declared identity.
        ref_key = (f"'{source_system}|{source_schema}|{source_table}|' "
                   f"|| md5(s::text)")

    select_columns = []

    # Envelope fields
    select_columns.append("gen_random_uuid() AS event_id")
    select_columns.append(
        f"uuid_generate_v5(\n"
        f"        '{UUID5_NAMESPACE}'::uuid,\n"
        f"        {ref_key}\n"
        f"    ) AS external_reference"
    )
    select_columns.append(f"'{source_system}' AS source_system")
    select_columns.append("'refresh' AS operation")
    if updated_at_column:
        select_columns.append(f"COALESCE(s.{updated_at_column}, NOW()) AS source_updated_at")
    else:
        select_columns.append("NOW() AS source_updated_at")
    select_columns.append("1 AS mapping_version")
    select_columns.append("encode(digest(s::text, 'sha256'), 'hex') AS payload_checksum")
    select_columns.append("TRUE AS active")
    select_columns.append("NOW() AS accepted_at")

    # Business columns from mappings
    envelope_fields = {
        "external_reference", "source_system", "operation",
        "source_updated_at", "mapping_version", "payload_checksum",
        "active", "accepted_at",
    }

    for m in mappings:
        source_col = m["source_column"]
        target_col = m.get("target_column")
        if not target_col or target_col in envelope_fields:
            continue

        transform = m.get("transform", "none")
        if transform == "trim":
            select_columns.append(f"TRIM(s.{source_col}) AS {target_col}")
        elif transform == "cast:date->datetime":
            select_columns.append(f"s.{source_col}::timestamp AS {target_col}")
        elif transform == "cast:str->int":
            select_columns.append(f"NULLIF(s.{source_col}, '')::integer AS {target_col}")
        elif transform == "cast:int->str":
            select_columns.append(f"s.{source_col}::text AS {target_col}")
        elif transform.startswith("enum_remap:"):
            remap = json.loads(transform.split("enum_remap:", 1)[1])
            case_parts = [f"WHEN s.{source_col} = '{k}' THEN '{v}'" for k, v in remap.items()]
            select_columns.append(
                f"CASE {' '.join(case_parts)} ELSE s.{source_col} END AS {target_col}"
            )
        else:
            select_columns.append(f"s.{source_col} AS {target_col}")

    columns_sql = ",\n    ".join(select_columns)
    sql = f"SELECT\n    {columns_sql}\nFROM {source_schema}.{source_table} s;"
    return sql


def fetch_and_bulk_insert(
    conn,
    staging,
    sql: str,
    target_table: str,
    columns: list[str],
    batch_size: int = 1000,
) -> int:
    """Fetch from PostgreSQL and bulk insert to MySQL."""
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    if not rows:
        return 0

    # Clamp out-of-range dates (year > 9999) to None for MySQL compatibility
    def _clamp_row(r):
        return tuple(
            None if isinstance(v, (datetime.date, datetime.datetime)) and v.year > 9999
            else v
            for v in r
        )
    rows = [_clamp_row(r) for r in rows]

    qt = _qt(target_table)
    placeholders = ", ".join(["%s"] * len(columns))
    insert_sql = (
        f"INSERT INTO {qt} ({', '.join(f'`{c}`' for c in columns)}) "
        f"VALUES ({placeholders})"
    )

    total = 0
    with staging.connection() as sconn:
        with sconn.cursor() as cur:
            for i in range(0, len(rows), batch_size):
                batch = rows[i : i + batch_size]
                cur.executemany(insert_sql, batch)
                total += len(batch)
                log.info("  Inserted %d/%d rows", total, len(rows))
        sconn.commit()

    return total


def drop_staging_table(staging, target_table: str):
    """Drop staging table if it exists."""
    qt = _qt(target_table)
    with staging.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {qt}")
        conn.commit()
    log.info("Dropped table: %s", target_table)
