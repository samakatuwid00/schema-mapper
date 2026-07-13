from src.schema_ingest import schema_fingerprint, table_schema
from src.schema_models import Column, Schema, Table
from src.services.view_proposer import _generate_view_sql


def test_entity_fingerprint_ignores_unrelated_tables():
    before = Schema("irimsv", [
        Table("schools", [Column("id", "integer", nullable=False)]),
    ])
    after = Schema("irimsv", [
        Table("schools", [Column("id", "integer", nullable=False)]),
        Table("schools_for_lrmis", [Column("id", "integer", nullable=False)]),
    ])

    assert schema_fingerprint(table_schema(before, "schools")) == \
           schema_fingerprint(table_schema(after, "schools"))


def test_entity_fingerprint_changes_when_its_table_changes():
    before = Schema("irimsv", [Table("schools", [Column("id", "integer")])])
    after = Schema("irimsv", [Table("schools", [
        Column("id", "integer"), Column("name", "string"),
    ])])

    assert schema_fingerprint(table_schema(before, "schools")) != \
           schema_fingerprint(table_schema(after, "schools"))


def test_generated_view_is_created_outside_authoritative_schema():
    sql = _generate_view_sql(
        "irimsv",
        "schools",
        "lrmis_projection",
        "schools_for_lrmis",
        [{"table": "schools", "column": "id", "alias": "id"}],
        [],
    )

    assert sql.startswith('CREATE OR REPLACE VIEW "lrmis_projection"."schools_for_lrmis" AS')
    assert 'FROM     "irimsv"."schools" "s"' in sql
