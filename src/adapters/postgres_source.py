"""Postgres source adapter (§2.2) — the read side of a migration.

Formalises source discovery + row streaming (previously ad-hoc): wraps the
existing `PostgresCentralConnector` and exposes schema, PK, count, and a batched
`fetch_rows` iterator the delivery/backfill paths can pull from.
"""
from __future__ import annotations

import os

import psycopg2.extras

from ..connectors import PostgresCentralConnector, safe_identifier


class PostgresSourceAdapter:
    engine_type = "postgres"

    def __init__(self, connector: PostgresCentralConnector | None = None,
                 schema: str | None = None):
        self._conn = connector or PostgresCentralConnector()
        self.schema = schema or os.environ.get("SOURCE_SCHEMA", "irimsv")

    def discover_schema(self):
        from ..schema_ingest import from_information_schema
        with self._conn.connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT table_name, column_name, data_type, is_nullable, ordinal_position
                    FROM information_schema.columns
                    WHERE table_schema = %s
                    ORDER BY table_name, ordinal_position
                """, (self.schema,))
                rows = [dict(r) for r in cur.fetchall()]
        return from_information_schema(rows, self.schema.upper())

    def count_rows(self, table: str) -> int:
        return self._conn.count_rows(self.schema, table)

    def get_pk_columns(self, table: str) -> list[str]:
        with self._conn.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON kcu.constraint_name = tc.constraint_name
                     AND kcu.table_schema = tc.table_schema
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                      AND tc.table_schema = %s AND tc.table_name = %s
                    ORDER BY kcu.ordinal_position
                """, (self.schema, table))
                return [r[0] for r in cur.fetchall()]

    def fetch_rows(self, table: str, columns: list[str] | None = None,
                   batch_size: int = 1000):
        safe_identifier(table)
        cols = "*" if not columns else ", ".join(f'"{safe_identifier(c)}"' for c in columns)
        with self._conn.connection() as conn:
            # A named (server-side) cursor streams large tables without loading
            # every row into memory at once.
            with conn.cursor(name=f"src_{table}",
                             cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.itersize = batch_size
                cur.execute(f'SELECT {cols} FROM "{self.schema}"."{table}"')
                for row in cur:
                    yield dict(row)

    def close(self) -> None:
        self._conn.close()
