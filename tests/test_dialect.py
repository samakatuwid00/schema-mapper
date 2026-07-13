"""Tests for the generic type system + dialects (§4)."""
import pytest

from src.dialect import (
    GenericType, native_to_generic, cast_value, get_dialect,
    MySQLDialect, PostgresDialect,
)
from src.adapters import MySQLTargetAdapter, PostgresTargetAdapter


def test_native_to_generic_bridges_engines():
    # same generic type from differently-spelled native types
    assert native_to_generic("mysql", "varchar(255)") is GenericType.STRING
    assert native_to_generic("postgres", "character varying") is GenericType.STRING
    assert native_to_generic("mysql", "bigint") is GenericType.INTEGER
    assert native_to_generic("postgres", "int8") is GenericType.INTEGER
    assert native_to_generic("postgres", "timestamp without time zone") is GenericType.DATETIME
    assert native_to_generic("postgres", "jsonb") is GenericType.JSON
    assert native_to_generic("postgres", "uuid") is GenericType.UUID


def test_native_to_generic_unknown_falls_back_to_string():
    assert native_to_generic("mysql", "geometry") is GenericType.STRING


def test_native_to_generic_unknown_engine_raises():
    with pytest.raises(ValueError):
        native_to_generic("oracle", "number")


def test_mysql_dialect_quotes_and_upserts():
    d = get_dialect("mysql")
    assert isinstance(d, MySQLDialect)
    assert d.quote("station") == "`station`"
    assert d.insert_sql("school", ["id", "name"]) == \
        "INSERT INTO `school` (`id`, `name`) VALUES (%s, %s)"
    sql = d.upsert_sql("school", ["ext", "name"], ["ext"])
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "`name` = VALUES(`name`)" in sql
    assert d.truncate_sql("school") == "TRUNCATE TABLE `school`"
    assert d.uuid_sql() == "UUID()"


def test_postgres_dialect_quotes_and_upserts():
    d = get_dialect("postgresql")
    assert isinstance(d, PostgresDialect)
    assert d.quote("school") == '"school"'
    assert d.insert_sql("school", ["id", "name"]) == \
        'INSERT INTO "school" ("id", "name") VALUES (%s, %s)'
    sql = d.upsert_sql("school", ["ext", "name"], ["ext"])
    assert 'ON CONFLICT ("ext") DO UPDATE SET' in sql
    assert '"name" = EXCLUDED."name"' in sql
    assert d.uuid_sql() == "gen_random_uuid()"


def test_postgres_upsert_all_keys_is_do_nothing():
    d = get_dialect("postgres")
    sql = d.upsert_sql("t", ["ext"], ["ext"])
    assert sql.endswith('ON CONFLICT ("ext") DO NOTHING')


def test_dialect_rejects_unsafe_identifier():
    d = get_dialect("mysql")
    with pytest.raises(ValueError):
        d.quote("school; DROP TABLE x")


def test_mssql_dialect_brackets_merge_and_ddl():
    from src.dialect import MSSQLDialect, GenericType
    d = get_dialect("mssql")
    assert isinstance(d, MSSQLDialect)
    assert d.quote("school") == "[school]"
    assert d.placeholder() == "?"
    assert d.insert_sql("school", ["id", "name"]) == \
        "INSERT INTO [school] ([id], [name]) VALUES (?, ?)"
    up = d.upsert_sql("school", ["ext", "name"], ["ext"])
    assert up.startswith("MERGE INTO [school] AS tgt")
    assert "WHEN MATCHED THEN UPDATE SET tgt.[name] = src.[name]" in up
    assert "WHEN NOT MATCHED THEN INSERT" in up
    assert d.generic_to_ddl(GenericType.STRING) == "NVARCHAR(MAX)"
    assert d.generic_to_ddl(GenericType.UUID) == "UNIQUEIDENTIFIER"
    assert d.uuid_sql() == "NEWID()"


def test_unknown_engine_has_no_dialect():
    with pytest.raises(ValueError):
        get_dialect("oracle")


def test_adapters_expose_matching_dialect():
    assert PostgresTargetAdapter(dsn="postgresql://x/y").dialect().engine == "postgres"
    assert MySQLTargetAdapter().dialect().engine == "mysql"


def test_cast_value_coerces_common_mismatches():
    assert cast_value("123", GenericType.INTEGER) == 123
    assert cast_value("12.5", GenericType.FLOAT) == 12.5
    assert cast_value("true", GenericType.BOOLEAN) is True
    assert cast_value(0, GenericType.BOOLEAN) is False
    assert cast_value(42, GenericType.STRING) == "42"
    assert cast_value(None, GenericType.INTEGER) is None


def test_cast_value_leaves_uncastable_or_opaque_untouched():
    assert cast_value("abc", GenericType.INTEGER) == "abc"      # not numeric -> unchanged
    assert cast_value("2026-07-13", GenericType.DATE) == "2026-07-13"
    assert cast_value({"a": 1}, GenericType.JSON) == {"a": 1}
