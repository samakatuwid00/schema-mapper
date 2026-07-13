"""generate_refresh_sql: primary-key handling, including id-less source tables.

Regression coverage for the "Drop & Restore" crash where an id-less source
table (no PRIMARY KEY, no ``id`` column) produced `column s.id does not exist`.
"""
from __future__ import annotations

from src.fast_refresh import generate_refresh_sql

_MAPPINGS = [{"source_column": "code", "target_column": "code", "transform": "none"}]


def _sql(pk):
    return generate_refresh_sql("irimsv", "nonprint_resource_sgl",
                                "irimsv_nonprint_resource_sgl_staging",
                                _MAPPINGS, "IRIMSV_REGION_V", pk)


def test_declared_primary_key_is_used():
    sql = _sql(["resource_id"])
    assert "s.resource_id::text" in sql
    assert "md5(" not in sql


def test_composite_primary_key_is_concatenated():
    sql = _sql(["region_id", "resource_id"])
    assert "s.region_id" in sql and "s.resource_id" in sql


def test_no_primary_key_falls_back_to_row_hash():
    # No PK and no id column: must not reference a named column that may not exist.
    sql = _sql([])
    assert "md5(s::text)" in sql
    assert "s.id" not in sql
    # still namespaced by system|schema|table
    assert "IRIMSV_REGION_V|irimsv|nonprint_resource_sgl|" in sql


def test_none_primary_key_also_hashes():
    assert "md5(s::text)" in _sql(None)
