"""Dialect-aware SQL generation (§4).

Isolates the per-engine SQL surface (identifier quoting, insert, upsert,
truncate, UUID) so the writer stays engine-agnostic. Each `TargetAdapter`
returns the matching `Dialect`; a `raw()` escape hatch covers anything the
protocol does not model.

Identifiers are validated with the same `safe_identifier` the connectors use —
the dialect never interpolates unchecked names into SQL.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..connectors import safe_identifier
from .types import GenericType


@runtime_checkable
class Dialect(Protocol):
    engine: str
    # True when a generated primary key is read back via `INSERT ... RETURNING`
    # (Postgres); False when it comes from the driver after insert (MySQL
    # `cursor.lastrowid`). This is the one difference the multi-table writer
    # must branch on to fill a child's FK from a just-inserted parent.
    supports_returning: bool

    def quote(self, identifier: str) -> str: ...
    def placeholder(self) -> str: ...
    def generic_to_ddl(self, generic_type: GenericType) -> str: ...
    def create_table_sql(self, table: str, columns: list[dict],
                         pk_columns: list[str] | None = None,
                         if_not_exists: bool = True) -> str: ...
    def insert_sql(self, table: str, columns: list[str]) -> str: ...
    def insert_returning_sql(self, table: str, columns: list[str], pk_column: str) -> str: ...
    def upsert_sql(self, table: str, columns: list[str], key_columns: list[str]) -> str: ...
    def truncate_sql(self, table: str) -> str: ...
    def uuid_sql(self) -> str: ...
    def raw(self, sql: str) -> str: ...


class _BaseDialect:
    engine = ""
    supports_returning = False
    _q = '"'  # quote char
    _GENERIC_DDL: dict = {}   # GenericType -> native column type

    def quote(self, identifier: str) -> str:
        safe_identifier(identifier)
        return f"{self._q}{identifier}{self._q}"

    def placeholder(self) -> str:
        return "%s"

    def generic_to_ddl(self, generic_type: GenericType) -> str:
        """Native column type for a `GenericType` in this engine (§3.3)."""
        return self._GENERIC_DDL[generic_type]

    def create_table_sql(self, table: str, columns: list[dict],
                         pk_columns: list[str] | None = None,
                         if_not_exists: bool = True) -> str:
        """CREATE TABLE from generic column specs `{name, type: GenericType,
        nullable}`, mapping each generic type to this engine's native type."""
        parts = []
        for c in columns:
            native = self.generic_to_ddl(c["type"])
            null = "" if c.get("nullable", True) else " NOT NULL"
            parts.append(f"{self.quote(c['name'])} {native}{null}")
        if pk_columns:
            parts.append(f"PRIMARY KEY ({', '.join(self.quote(p) for p in pk_columns)})")
        ine = "IF NOT EXISTS " if if_not_exists else ""
        return (f"CREATE TABLE {ine}{self.quote(table)} (\n  "
                + ",\n  ".join(parts) + "\n)")

    def _cols(self, columns: list[str]) -> str:
        return ", ".join(self.quote(c) for c in columns)

    def _values(self, columns: list[str]) -> str:
        return ", ".join([self.placeholder()] * len(columns))

    def insert_sql(self, table: str, columns: list[str]) -> str:
        return (f"INSERT INTO {self.quote(table)} ({self._cols(columns)}) "
                f"VALUES ({self._values(columns)})")

    def insert_returning_sql(self, table: str, columns: list[str], pk_column: str) -> str:
        # Engines without RETURNING ignore the pk_column and read it back from
        # the driver (e.g. lastrowid); the plain insert is correct for them.
        return self.insert_sql(table, columns)

    def truncate_sql(self, table: str) -> str:
        return f"TRUNCATE TABLE {self.quote(table)}"

    def raw(self, sql: str) -> str:
        return sql


_MYSQL_DDL = {
    GenericType.STRING: "TEXT", GenericType.INTEGER: "INT",
    GenericType.FLOAT: "DOUBLE", GenericType.BOOLEAN: "TINYINT(1)",
    GenericType.DATE: "DATE", GenericType.DATETIME: "DATETIME",
    GenericType.BINARY: "BLOB", GenericType.JSON: "JSON", GenericType.UUID: "CHAR(36)",
}

_POSTGRES_DDL = {
    GenericType.STRING: "TEXT", GenericType.INTEGER: "INTEGER",
    GenericType.FLOAT: "DOUBLE PRECISION", GenericType.BOOLEAN: "BOOLEAN",
    GenericType.DATE: "DATE", GenericType.DATETIME: "TIMESTAMP",
    GenericType.BINARY: "BYTEA", GenericType.JSON: "JSONB", GenericType.UUID: "UUID",
}


class MySQLDialect(_BaseDialect):
    engine = "mysql"
    _q = "`"
    _GENERIC_DDL = _MYSQL_DDL

    def upsert_sql(self, table: str, columns: list[str], key_columns: list[str]) -> str:
        keys = set(key_columns)
        updates = [f"{self.quote(c)} = VALUES({self.quote(c)})"
                   for c in columns if c not in keys] or \
                  [f"{self.quote(key_columns[0])} = {self.quote(key_columns[0])}"]
        return (f"{self.insert_sql(table, columns)} "
                f"ON DUPLICATE KEY UPDATE {', '.join(updates)}")

    def uuid_sql(self) -> str:
        return "UUID()"


class PostgresDialect(_BaseDialect):
    engine = "postgres"
    supports_returning = True
    _q = '"'
    _GENERIC_DDL = _POSTGRES_DDL

    def insert_returning_sql(self, table: str, columns: list[str], pk_column: str) -> str:
        return f"{self.insert_sql(table, columns)} RETURNING {self.quote(pk_column)}"

    def upsert_sql(self, table: str, columns: list[str], key_columns: list[str]) -> str:
        if not key_columns:
            raise ValueError("Postgres upsert needs at least one conflict key column")
        keys = set(key_columns)
        conflict = ", ".join(self.quote(c) for c in key_columns)
        updates = [f"{self.quote(c)} = EXCLUDED.{self.quote(c)}"
                   for c in columns if c not in keys]
        if not updates:
            return f"{self.insert_sql(table, columns)} ON CONFLICT ({conflict}) DO NOTHING"
        return (f"{self.insert_sql(table, columns)} "
                f"ON CONFLICT ({conflict}) DO UPDATE SET {', '.join(updates)}")

    def uuid_sql(self) -> str:
        return "gen_random_uuid()"


_MSSQL_DDL = {
    GenericType.STRING: "NVARCHAR(MAX)", GenericType.INTEGER: "INT",
    GenericType.FLOAT: "FLOAT", GenericType.BOOLEAN: "BIT",
    GenericType.DATE: "DATE", GenericType.DATETIME: "DATETIME2",
    GenericType.BINARY: "VARBINARY(MAX)", GenericType.JSON: "NVARCHAR(MAX)",
    GenericType.UUID: "UNIQUEIDENTIFIER",
}


class MSSQLDialect(_BaseDialect):
    engine = "mssql"
    supports_returning = False        # uses OUTPUT / SCOPE_IDENTITY(), not RETURNING
    _GENERIC_DDL = _MSSQL_DDL

    def quote(self, identifier: str) -> str:
        safe_identifier(identifier)
        return f"[{identifier}]"

    def placeholder(self) -> str:
        return "?"                     # pyodbc parameter marker

    def upsert_sql(self, table: str, columns: list[str], key_columns: list[str]) -> str:
        if not key_columns:
            raise ValueError("MSSQL MERGE needs at least one key column")
        q = self.quote
        keys = set(key_columns)
        src_cols = ", ".join(q(c) for c in columns)
        placeholders = ", ".join([self.placeholder()] * len(columns))
        on = " AND ".join(f"tgt.{q(k)} = src.{q(k)}" for k in key_columns)
        updates = ", ".join(f"tgt.{q(c)} = src.{q(c)}" for c in columns if c not in keys)
        matched = f"WHEN MATCHED THEN UPDATE SET {updates} " if updates else ""
        ins_cols = ", ".join(q(c) for c in columns)
        ins_vals = ", ".join(f"src.{q(c)}" for c in columns)
        return (f"MERGE INTO {q(table)} AS tgt "
                f"USING (VALUES ({placeholders})) AS src ({src_cols}) ON {on} "
                f"{matched}WHEN NOT MATCHED THEN INSERT ({ins_cols}) VALUES ({ins_vals});")

    def uuid_sql(self) -> str:
        return "NEWID()"


_DIALECTS = {
    "mysql": MySQLDialect, "mariadb": MySQLDialect,
    "postgres": PostgresDialect, "postgresql": PostgresDialect, "pg": PostgresDialect,
    "mssql": MSSQLDialect, "sqlserver": MSSQLDialect,
}


def get_dialect(engine: str) -> Dialect:
    cls = _DIALECTS.get((engine or "").strip().lower())
    if cls is None:
        raise ValueError(f"no dialect for engine {engine!r}")
    return cls()
