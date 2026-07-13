"""Tests for generic_to_ddl + target-schema creation (§3.3)."""
from src.dialect import GenericType, get_dialect
from src.delivery.schema import (
    create_delivery_audit_sql, create_target_ddl,
)
from src.lrmis_registry import LrmisRegistry


def _col(t, n, p, key="", extra="", nullable="YES", dtype="int"):
    return {"table_name": t, "column_name": n, "data_type": dtype,
            "is_nullable": nullable, "ordinal_position": p,
            "column_key": key, "extra": extra, "column_default": None}


def test_generic_to_ddl_per_engine():
    pg, my = get_dialect("postgres"), get_dialect("mysql")
    assert pg.generic_to_ddl(GenericType.STRING) == "TEXT"
    assert pg.generic_to_ddl(GenericType.DATETIME) == "TIMESTAMP"
    assert pg.generic_to_ddl(GenericType.JSON) == "JSONB"
    assert pg.generic_to_ddl(GenericType.BOOLEAN) == "BOOLEAN"
    assert my.generic_to_ddl(GenericType.BOOLEAN) == "TINYINT(1)"
    assert my.generic_to_ddl(GenericType.JSON) == "JSON"


def test_create_table_sql_postgres():
    d = get_dialect("postgres")
    sql = d.create_table_sql("school", [
        {"name": "id", "type": GenericType.INTEGER, "nullable": False},
        {"name": "name", "type": GenericType.STRING, "nullable": False},
    ], pk_columns=["id"])
    assert 'CREATE TABLE IF NOT EXISTS "school"' in sql
    assert '"id" INTEGER NOT NULL' in sql
    assert '"name" TEXT NOT NULL' in sql
    assert 'PRIMARY KEY ("id")' in sql


def test_create_delivery_audit_sql_is_postgres_flavored():
    sql = create_delivery_audit_sql(get_dialect("postgres"))
    assert 'CREATE TABLE IF NOT EXISTS "delivery_audit"' in sql
    assert '"event_id" TEXT NOT NULL' in sql
    assert 'PRIMARY KEY ("event_id")' in sql
    assert "TIMESTAMP" in sql            # source_updated_at / accepted_at


def test_create_target_ddl_parents_first_maps_types():
    # registry native types are MySQL; create on a Postgres target
    reg = LrmisRegistry.from_discovery([
        _col("region", "id", 1, key="PRI", extra="auto_increment"),
        _col("region", "label", 2, dtype="varchar(50)", nullable="NO"),
        _col("school", "id", 1, key="PRI", extra="auto_increment"),
        _col("school", "region_id", 2, dtype="int", nullable="NO"),
    ], [{"table_name": "school", "column_name": "region_id",
         "ref_table": "region", "ref_column": "id"}])
    ddl = create_target_ddl(get_dialect("postgres"), reg, source_engine="mysql")
    joined = "\n".join(ddl)
    # parents first: region created before school
    assert joined.index('"region"') < joined.index('"school"')
    # varchar(50) -> generic STRING -> Postgres TEXT
    assert '"label" TEXT NOT NULL' in joined
    assert '"region_id" INTEGER NOT NULL' in joined
