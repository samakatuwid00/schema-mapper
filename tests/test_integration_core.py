from datetime import datetime, timezone
from uuid import uuid4

from src.integration_store import checksum
from src.schema_ingest import from_information_schema, schema_fingerprint
from src.schema_models import Column, Schema, Table
from src.transform_engine import transform_row
from src.worker import _outbound_row
from src.connectors import MySQLStagingConnector


def test_fingerprint_is_stable():
    schema = Schema("LRMIS", [Table("items", [Column("id", "integer")])])
    assert schema_fingerprint(schema) == schema_fingerprint(schema)


def test_mysql_information_schema_normalization():
    result = from_information_schema([{
        "TABLE_NAME": "items", "COLUMN_NAME": "id", "DATA_TYPE": "bigint",
        "IS_NULLABLE": "NO", "COLUMN_KEY": "PRI",
    }], "LRMIS")
    column = result.tables[0].columns[0]
    assert (column.data_type, column.nullable, column.is_primary_key) == ("integer", False, True)


def test_envelope_fields_are_supplied_after_business_validation():
    target = Schema("LRMIS", [Table("irimsv_customer_staging", [
        Column("event_id", "string", nullable=False),
        Column("external_reference", "string", nullable=False),
        Column("cust_nm", "string", nullable=False),
    ])])
    config = {"mappings": [{"source_column": "name", "target_table": "irimsv_customer_staging",
                            "target_column": "cust_nm", "transform": "none"}]}
    row, errors = transform_row({"name": "Teacher"}, config, target)
    assert row == {"cust_nm": "Teacher"}
    assert errors == []


def test_outbound_contract_and_soft_deactivation():
    event = {
        "event_id": uuid4(), "external_reference": uuid4(), "source_system": "IRIMSV_REGION_V",
        "operation": "deactivate", "source_updated_at": datetime.now(timezone.utc),
        "payload_checksum": checksum({"id": 1}),
    }
    output = _outbound_row(event, {"version": 3}, {"cust_nm": "Teacher"})
    assert output["active"] is False
    assert output["mapping_version"] == 3
    assert output["external_reference"] == str(event["external_reference"])


def test_mysql_identifier_validation_happens_before_connection():
    connector = MySQLStagingConnector({})
    try:
        connector.upsert("unsafe;drop", {"external_reference": "x"})
    except ValueError as error:
        assert "unsafe target table" in str(error)
    else:
        raise AssertionError("unsafe identifier was accepted")
