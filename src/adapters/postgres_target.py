"""Postgres target adapter — discovers a live Postgres target.

This is how a `pg_dump`/`.backup` target (e.g. old-lrmis.backup) is ingested:
restore it into a Postgres database, then point this adapter at it. The binary
archive is never parsed; the structure comes from `information_schema`.

Postgres does not carry MySQL's `column_key`/`extra` columns, so
`normalize_pg_columns` derives them: PRIMARY KEY membership from
`table_constraints`, and auto-increment from identity columns or a
`nextval(...)` (serial) default. The result matches the common discovery
row shape, so `LrmisRegistry.from_discovery` treats it exactly like MySQL.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2 import sql

from ._protocols import _DiscoveryMixin

_COLS_SQL = """
    SELECT c.table_name, c.column_name, c.data_type, c.is_nullable,
           c.ordinal_position, c.column_default, c.is_identity
    FROM information_schema.columns c
    JOIN information_schema.tables t
      ON t.table_schema = c.table_schema AND t.table_name = c.table_name
    WHERE c.table_schema = %s AND t.table_type = 'BASE TABLE'
    ORDER BY c.table_name, c.ordinal_position
"""

_PK_SQL = """
    SELECT kcu.table_name, kcu.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema = kcu.table_schema
    WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = %s
"""

_FK_SQL = """
    SELECT tc.table_name AS table_name,
           kcu.column_name AS column_name,
           ccu.table_name AS ref_table,
           ccu.column_name AS ref_column
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema = kcu.table_schema
    JOIN information_schema.constraint_column_usage ccu
      ON ccu.constraint_name = tc.constraint_name
     AND ccu.table_schema = tc.table_schema
    WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = %s
"""


def normalize_pg_columns(col_rows: list[dict],
                         pk_columns: set[tuple[str, str]]) -> list[dict]:
    """Map Postgres information_schema rows into the common discovery shape.

    Marks PRIMARY KEY columns (`column_key='PRI'`) and auto-generated columns
    (`extra='auto_increment'` for IDENTITY or serial `nextval(...)` defaults) so
    the registry reads them the same way it reads MySQL.
    """
    out: list[dict] = []
    for row in col_rows:
        r = {k.lower(): v for k, v in row.items()}
        table, name = r["table_name"], r["column_name"]
        default = r.get("column_default")
        is_identity = str(r.get("is_identity") or "").upper() == "YES"
        is_serial = bool(default) and str(default).lower().startswith("nextval(")
        out.append({
            "table_name": table,
            "column_name": name,
            "data_type": r["data_type"],
            "is_nullable": r.get("is_nullable"),
            "ordinal_position": r["ordinal_position"],
            "column_default": default,
            "column_key": "PRI" if (table, name) in pk_columns else "",
            "extra": "auto_increment" if (is_identity or is_serial) else "",
        })
    return out


class PostgresTargetAdapter(_DiscoveryMixin):
    engine_type = "postgres"

    def __init__(self, dsn: str | None = None, schema: str | None = None):
        self.dsn = dsn or os.environ.get(
            "LRMIS_TARGET_PG_DSN",
            "postgresql://postgres:postgres@localhost:5432/lrmis_target")
        # The restored old-lrmis dump lives in schema `lrmis`; the worker and
        # schema-swap construct the adapter with no explicit schema, so fall back
        # to LRMIS_TARGET_PG_SCHEMA (default `public` for a vanilla target).
        self.schema = schema or os.environ.get("LRMIS_TARGET_PG_SCHEMA", "public")

    def _rows(self, sql: str, params: tuple) -> list[dict]:
        conn = psycopg2.connect(self.dsn)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def column_rows(self) -> list[dict]:
        cols = self._rows(_COLS_SQL, (self.schema,))
        pk = {(r["table_name"], r["column_name"])
              for r in self._rows(_PK_SQL, (self.schema,))}
        return normalize_pg_columns(cols, pk)

    def fk_rows(self) -> list[dict]:
        return self._rows(_FK_SQL, (self.schema,))

    @contextmanager
    def connection(self):
        """An open connection to the target, for the generic writer's delivery
        pass (one connection reused across a batch, committed per entity).

        The writer emits unqualified table names, so the session `search_path`
        is pinned to the adapter's schema (the restored `.backup` lives in
        `lrmis`, not `public`); without this every write hits `relation ...
        does not exist`."""
        conn = psycopg2.connect(self.dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SET search_path TO {}, public").format(
                        sql.Identifier(self.schema)))
            conn.commit()
            yield conn
        finally:
            conn.close()
