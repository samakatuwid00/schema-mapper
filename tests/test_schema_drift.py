from src.schema_drift import diff_schemas
from src.schema_models import Column, Schema, Table


def schema(columns):
    return Schema("LRMIS", [Table("records", columns)])


def test_optional_column_is_non_breaking():
    before = schema([Column("id", "integer", nullable=False, is_primary_key=True)])
    after = schema([Column("id", "integer", nullable=False, is_primary_key=True),
                    Column("note", "string", nullable=True)])
    assert diff_schemas(before, after)[0]["breaking"] is False


def test_required_column_is_breaking():
    before = schema([Column("id", "integer", nullable=False, is_primary_key=True)])
    after = schema([Column("id", "integer", nullable=False, is_primary_key=True),
                    Column("required", "string", nullable=False)])
    assert diff_schemas(before, after)[0]["breaking"] is True


def test_type_change_and_removal_are_breaking():
    before = schema([Column("id", "integer", nullable=False), Column("old", "string")])
    after = schema([Column("id", "string", nullable=False)])
    changes = diff_schemas(before, after)
    assert {change["kind"] for change in changes} == {"column_changed", "column_removed"}
    assert all(change["breaking"] for change in changes)
