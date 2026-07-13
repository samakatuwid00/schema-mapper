"""Database adapters for the authoritative PostgreSQL DB and LRMIS MySQL staging."""
from __future__ import annotations

import os
import json
import datetime
from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extras
from psycopg2 import pool

MAX_PAGE_SIZE = 100


def safe_identifier(name: str) -> str:
    """Reject anything that is not a bare SQL identifier.

    Callers must additionally allowlist the name against information_schema;
    this is the last line of defence, not the only one.
    """
    if not name or not name.replace("_", "").isalnum():
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


def _sort_clause(sort: str | None, direction: str) -> str:
    if not sort:
        return ""
    if direction.lower() not in ("asc", "desc"):
        raise ValueError(f"unsafe sort direction: {direction!r}")
    return f" ORDER BY {{sort}} {direction.upper()}"


def _clamp_mysql_dates(row: dict) -> dict:
    """Replace out-of-range Python date/datetime objects (year > 9999) with None."""
    return {
        k: None if isinstance(v, (datetime.date, datetime.datetime)) and v.year > 9999
        else v
        for k, v in row.items()
    }


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

    # -- read-only helpers for the data browser -----------------------------
    # SELECT only. No user SQL reaches these; identifiers are allowlisted by
    # the caller against information_schema and re-checked here.

    def count_rows(self, schema: str, table: str) -> int:
        safe_identifier(schema)
        safe_identifier(table)
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f'SELECT count(*) FROM "{schema}"."{table}"')
                return cur.fetchone()[0]

    def fetch_rows(self, schema: str, table: str, limit: int, offset: int,
                   sort: str | None = None, direction: str = "asc") -> list[dict]:
        safe_identifier(schema)
        safe_identifier(table)
        limit = max(1, min(int(limit), MAX_PAGE_SIZE))
        offset = max(0, int(offset))
        order = ""
        if sort:
            safe_identifier(sort)
            order = _sort_clause(sort, direction).format(sort=f'"{sort}"')
        sql = f'SELECT * FROM "{schema}"."{table}"{order} LIMIT %s OFFSET %s'
        with self.connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (limit, offset))
                return [dict(row) for row in cur.fetchall()]

    def fetch_row_by(self, schema: str, table: str, column: str, value) -> dict | None:
        safe_identifier(schema)
        safe_identifier(table)
        safe_identifier(column)
        sql = f'SELECT * FROM "{schema}"."{table}" WHERE "{column}" = %s LIMIT 1'
        with self.connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (value,))
                row = cur.fetchone()
                return dict(row) if row else None


VIEWS_DATABASE = "lrmis_staging_views"


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

    @classmethod
    def for_database(cls, database: str) -> "MySQLStagingConnector":
        """A connector to a different database on the same server (same creds).

        Used to reach the Path B `lrmis_target` database while the default
        connector keeps serving `lrmis_staging`."""
        config = cls._environment_config()
        config["database"] = database
        return cls(config)

    @classmethod
    def for_target(cls) -> "MySQLStagingConnector":
        return cls.for_database(os.environ.get("LRMIS_TARGET_DATABASE", "lrmis_target"))

    @staticmethod
    def is_views_table(table: str) -> bool:
        return "_for_lrmis" in table

    def _qt(self, table: str) -> str:
        """Return a qualified table name, optionally database-prefixed for views."""
        safe_identifier(table)
        if self.is_views_table(table):
            return f"`{VIEWS_DATABASE}`.`{table}`"
        return f"`{table}`"

    def _ensure_pool(self):
        if self._pool is None:
            from mysql.connector.pooling import MySQLConnectionPool
            # Pool names are process-global in mysql.connector, so derive it from
            # the database — otherwise a second connector (lrmis_target) collides
            # with the lrmis_staging pool.
            pool_name = f"lrmis_{self.config.get('database', 'staging')}"
            self._pool = MySQLConnectionPool(pool_name=pool_name, pool_size=5, **self.config)

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
        qt = self._qt(table)
        sql = (
            f"INSERT INTO {qt} ({', '.join(f'`{c}`' for c in columns)}) "
            f"VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {', '.join(updates)}"
        )
        row = _clamp_mysql_dates(row)
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

        qt = self._qt(table)
        placeholders = ", ".join(["%s"] * len(columns))
        sql = (
            f"INSERT INTO {qt} ({', '.join(f'`{c}`' for c in columns)}) "
            f"VALUES ({placeholders})"
        )

        total = 0
        with self.connection() as conn:
            with conn.cursor() as cur:
                for i in range(0, len(rows), batch_size):
                    batch = [_clamp_mysql_dates(r) for r in rows[i : i + batch_size]]
                    values = [
                        [json.dumps(row[c], default=str) if isinstance(row[c], (dict, list)) else row[c] for c in columns]
                        for row in batch
                    ]
                    cur.executemany(sql, values)
                    total += len(batch)
            conn.commit()

        return total

    def information_schema(self, schema_name: str | None = None) -> list[dict]:
        if schema_name is None:
            schema_name = self.config["database"]
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

    def table_names(self, schema_name: str | None = None) -> list[str]:
        """Base-table names in the connector's database (one row per table).

        Cheaper and clearer than deriving names from ``information_schema()``
        (which returns one row per *column*) when only the table set is needed."""
        if schema_name is None:
            schema_name = self.config["database"]
        sql = """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (schema_name,))
                return [row[0] for row in cur.fetchall()]

    def close(self) -> None:
        """Interface symmetry with PostgresCentralConnector.

        Pooled MySQL connections are returned to the pool at the end of each
        ``connection()`` context; mysql-connector-python's pool exposes no clean
        close-all, so this is intentionally a no-op."""
        return None

    # -- read-only helpers for the data browser -----------------------------
    # This class stays a least-privilege writer: these issue SELECT only and
    # accept no user-supplied SQL. Identifiers are allowlisted by the caller.

    def count_rows(self, table: str) -> int:
        qt = self._qt(table)
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM {qt}")
                return cur.fetchone()[0]

    def fetch_rows(self, table: str, limit: int, offset: int,
                   sort: str | None = None, direction: str = "asc") -> list[dict]:
        qt = self._qt(table)
        limit = max(1, min(int(limit), MAX_PAGE_SIZE))
        offset = max(0, int(offset))
        order = ""
        if sort:
            safe_identifier(sort)
            order = _sort_clause(sort, direction).format(sort=f"`{sort}`")
        sql = f"SELECT * FROM {qt}{order} LIMIT %s OFFSET %s"
        with self.connection() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(sql, (limit, offset))
                return cur.fetchall()

    def fetch_row_by(self, table: str, column: str, value) -> dict | None:
        qt = self._qt(table)
        safe_identifier(column)
        sql = f"SELECT * FROM {qt} WHERE `{column}` = %s LIMIT 1"
        with self.connection() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(sql, (value,))
                rows = cur.fetchall()
                return rows[0] if rows else None
