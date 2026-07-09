"""Database adapters for the authoritative PostgreSQL DB and LRMIS MySQL staging."""
from __future__ import annotations

import os
import json
from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extras
from psycopg2 import pool


class PostgresCentralConnector:
    def __init__(self, dsn: str | None = None, minimum: int = 1, maximum: int = 5):
        self.dsn = dsn or os.environ.get(
            "CENTRAL_DB_URL", "postgresql://postgres:postgres@localhost:5433/central"
        )
        self._pool = pool.ThreadedConnectionPool(minimum, maximum, self.dsn)

    @contextmanager
    def connection(self) -> Iterator:
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            self._pool.putconn(conn)

    def close(self):
        self._pool.closeall()


class MySQLStagingConnector:
    """Least-privilege writer. It never creates or alters LRMIS tables."""

    def __init__(self, config: dict | None = None):
        self.config = config or self._environment_config()
        self._pool = None

    @staticmethod
    def _environment_config() -> dict:
        return {
            "host": os.environ.get("LRMIS_STAGING_HOST", "localhost"),
            "port": int(os.environ.get("LRMIS_STAGING_PORT", "3307")),
            "database": os.environ.get("LRMIS_STAGING_DATABASE", "lrmis_staging"),
            "user": os.environ.get("LRMIS_STAGING_USER", "irimsv_writer"),
            "password": os.environ.get("LRMIS_STAGING_PASSWORD", "change-me"),
            "ssl_disabled": os.environ.get("LRMIS_STAGING_SSL_DISABLED", "false").lower() == "true",
        }

    def _ensure_pool(self):
        if self._pool is None:
            from mysql.connector.pooling import MySQLConnectionPool
            self._pool = MySQLConnectionPool(pool_name="lrmis_staging", pool_size=5, **self.config)

    @contextmanager
    def connection(self) -> Iterator:
        self._ensure_pool()
        conn = self._pool.get_connection()
        try:
            yield conn
        finally:
            conn.close()

    def upsert(self, table: str, row: dict, unique_column: str = "external_reference"):
        if not table.replace("_", "").isalnum():
            raise ValueError("unsafe target table name")
        if unique_column not in row:
            raise ValueError(f"missing idempotency field: {unique_column}")
        columns = list(row)
        for column in columns:
            if not column.replace("_", "").isalnum():
                raise ValueError(f"unsafe target column name: {column}")
        placeholders = ", ".join(["%s"] * len(columns))
        # A retried older event must never overwrite newer LRMIS staging state.
        # Keep source_updated_at last because MySQL evaluates assignments left-to-right.
        update_columns = [c for c in columns if c not in {unique_column, "source_updated_at"}]
        if "source_updated_at" in columns:
            updates = [
                f"`{column}` = IF(VALUES(`source_updated_at`) >= `source_updated_at`, "
                f"VALUES(`{column}`), `{column}`)" for column in update_columns
            ]
            updates.append(
                "`source_updated_at` = GREATEST(VALUES(`source_updated_at`), `source_updated_at`)"
            )
        else:
            updates = [f"`{column}` = VALUES(`{column}`)" for column in update_columns]
        sql = (
            f"INSERT INTO `{table}` ({', '.join(f'`{c}`' for c in columns)}) "
            f"VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {', '.join(updates)}"
        )
        with self.connection() as conn:
            with conn.cursor() as cur:
                values = [json.dumps(row[c], default=str) if isinstance(row[c], (dict, list)) else row[c]
                          for c in columns]
                cur.execute(sql, values)
            conn.commit()

    def bulk_insert(self, table: str, rows: list[dict], batch_size: int = 1000) -> int:
        """Bulk insert using executemany for better performance."""
        if not rows:
            return 0

        columns = list(rows[0].keys())
        for column in columns:
            if not column.replace("_", "").isalnum():
                raise ValueError(f"unsafe target column name: {column}")

        placeholders = ", ".join(["%s"] * len(columns))
        sql = (
            f"INSERT INTO `{table}` ({', '.join(f'`{c}`' for c in columns)}) "
            f"VALUES ({placeholders})"
        )

        total = 0
        with self.connection() as conn:
            with conn.cursor() as cur:
                for i in range(0, len(rows), batch_size):
                    batch = rows[i : i + batch_size]
                    values = [
                        [json.dumps(row[c], default=str) if isinstance(row[c], (dict, list)) else row[c] for c in columns]
                        for row in batch
                    ]
                    cur.executemany(sql, values)
                    total += len(batch)
            conn.commit()

        return total

    def information_schema(self, schema_name: str | None = None) -> list[dict]:
        schema_name = schema_name or self.config["database"]
        sql = """
            SELECT table_name, column_name, data_type, is_nullable, column_key,
                   ordinal_position, column_default, extra
            FROM information_schema.columns
            WHERE table_schema = %s
            ORDER BY table_name, ordinal_position
        """
        with self.connection() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(sql, (schema_name,))
                return cur.fetchall()
