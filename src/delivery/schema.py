"""Create a target schema from the generic type system (§3.3 / target setup).

`create_target_ddl` builds `CREATE TABLE` for every registry table, parents-first,
mapping each column's native type through the generic type system to the target
dialect — so a target discovered/parsed as one engine can be created on another.

`create_delivery_audit_sql` builds the pipeline's audit envelope for the target
engine (the streaming path needs it to exist; a restored `.backup` won't have
it). Applying the DDL is a separate, explicit call.
"""
from __future__ import annotations

from ..dialect import GenericType, native_to_generic

# The delivery-audit envelope as engine-neutral generic columns.
_AUDIT_COLUMNS = [
    {"name": "event_id", "type": GenericType.STRING, "nullable": False},
    {"name": "external_reference", "type": GenericType.STRING, "nullable": False},
    {"name": "source_system", "type": GenericType.STRING, "nullable": True},
    {"name": "operation", "type": GenericType.STRING, "nullable": True},
    {"name": "source_updated_at", "type": GenericType.DATETIME, "nullable": True},
    {"name": "mapping_version", "type": GenericType.INTEGER, "nullable": True},
    {"name": "payload_checksum", "type": GenericType.STRING, "nullable": True},
    {"name": "active", "type": GenericType.INTEGER, "nullable": True},
    {"name": "accepted_at", "type": GenericType.DATETIME, "nullable": True},
]


def create_delivery_audit_sql(dialect) -> str:
    return dialect.create_table_sql("delivery_audit", _AUDIT_COLUMNS,
                                    pk_columns=["event_id"])


def create_target_ddl(dialect, registry, source_engine: str) -> list[str]:
    """`CREATE TABLE` for every registry table, parents-first, with each column's
    native type mapped through the generic type system to `dialect`'s engine."""
    out: list[str] = []
    for name in registry.topological_order():
        table = registry.get_table(name)
        columns = [{"name": c.name,
                    "type": native_to_generic(source_engine, c.data_type),
                    "nullable": c.nullable}
                   for c in table.columns]
        out.append(dialect.create_table_sql(name, columns,
                                            pk_columns=table.primary_key))
    return out


def create_target_schema(target_conn, dialect, registry, source_engine: str,
                         *, with_audit: bool = True) -> int:
    """Execute the target DDL against an open connection. Returns table count.
    Explicit/destructive-adjacent: only run against a target you intend to build."""
    statements = create_target_ddl(dialect, registry, source_engine)
    if with_audit:
        statements.append(create_delivery_audit_sql(dialect))
    with target_conn.cursor() as cur:
        for sql in statements:
            cur.execute(sql)
    return len(statements)
