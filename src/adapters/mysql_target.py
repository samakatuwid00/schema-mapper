"""MySQL target adapter — discovers the live MySQL target (default lrmis_target).

Wraps the existing least-privilege `MySQLStagingConnector`; discovery is
SELECT-only against `information_schema`, consistent with that connector's other
read helpers.
"""
from __future__ import annotations

from ..connectors import MySQLStagingConnector
from ._protocols import _DiscoveryMixin

# MySQL exposes FKs inline in key_column_usage (referenced_* is NULL for
# non-FK key columns).
_FK_SQL = """
    SELECT table_name AS table_name,
           column_name AS column_name,
           referenced_table_name AS ref_table,
           referenced_column_name AS ref_column
    FROM information_schema.key_column_usage
    WHERE table_schema = %s AND referenced_table_name IS NOT NULL
    ORDER BY table_name, ordinal_position
"""


class MySQLTargetAdapter(_DiscoveryMixin):
    engine_type = "mysql"

    def __init__(self, connector: MySQLStagingConnector | None = None,
                 database: str | None = None):
        self._conn = connector or MySQLStagingConnector.for_target()
        self._database = database or self._conn.config.get("database")

    def column_rows(self) -> list[dict]:
        # information_schema() already returns the common column-row shape
        # (table_name, column_name, data_type, is_nullable, column_key,
        # ordinal_position, column_default, extra).
        return self._conn.information_schema(self._database)

    def fk_rows(self) -> list[dict]:
        with self._conn.connection() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(_FK_SQL, (self._database,))
                return list(cur.fetchall())

    def close(self) -> None:
        self._conn.close()
