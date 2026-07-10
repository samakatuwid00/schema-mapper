"""Data browser: allowlisting, clamping, and identifier safety.

Runs without a database - the connectors are stubbed and the allowlist is
injected, so what is exercised is the validation layer that stands between a
request and any SQL.
"""
from __future__ import annotations

import pytest

from src import connectors
from src.services import data_browser
from src.services.common import NotFoundError, ValidationError

SOURCE_TABLES = {
    "farmers": [
        {"name": "id", "data_type": "integer", "nullable": False, "is_primary_key": True},
        {"name": "full_name", "data_type": "text", "nullable": True, "is_primary_key": False},
    ]
}
TARGET_TABLES = {
    "irimsv_farmers_staging": [
        {"name": "external_reference", "data_type": "char", "nullable": False,
         "is_primary_key": False},
    ]
}


@pytest.fixture()
def allowlist(monkeypatch):
    monkeypatch.setattr(data_browser, "_source_columns", lambda central, schema: SOURCE_TABLES)
    monkeypatch.setattr(data_browser, "_target_columns", lambda staging: TARGET_TABLES)


class _FakeCentral:
    def __init__(self):
        self.calls = []

    def count_rows(self, schema, table):
        self.calls.append(("count", schema, table))
        return 3

    def fetch_rows(self, schema, table, limit, offset, sort=None, direction="asc"):
        self.calls.append(("fetch", schema, table, limit, offset, sort, direction))
        return [{"id": 1, "full_name": "Ada"}]

    def close(self):
        pass


class _FakeStaging:
    def count_rows(self, table):
        return 1

    def fetch_rows(self, table, limit, offset, sort=None, direction="asc"):
        return [{"external_reference": "uuid-1"}]


# ---------------------------------------------------------------------------
# Allowlisting
# ---------------------------------------------------------------------------

def test_unknown_table_is_rejected_before_any_sql(allowlist):
    central = _FakeCentral()
    with pytest.raises(NotFoundError):
        data_browser.fetch_rows("source", "pg_shadow", central=central,
                                staging=_FakeStaging())
    assert central.calls == []  # nothing touched the database


def test_sort_column_not_in_table_is_rejected(allowlist):
    central = _FakeCentral()
    with pytest.raises(ValidationError):
        data_browser.fetch_rows("source", "farmers", sort="1;DROP TABLE farmers",
                                central=central, staging=_FakeStaging())
    assert central.calls == []


def test_unknown_side_is_rejected(allowlist):
    with pytest.raises(ValidationError):
        data_browser.fetch_rows("prod", "farmers", central=_FakeCentral(),
                                staging=_FakeStaging())


def test_bad_direction_is_rejected(allowlist):
    with pytest.raises(ValidationError):
        data_browser.fetch_rows("source", "farmers", direction="; DROP",
                                central=_FakeCentral(), staging=_FakeStaging())


# ---------------------------------------------------------------------------
# Clamping and paging
# ---------------------------------------------------------------------------

def test_page_size_is_clamped_not_rejected(allowlist):
    central = _FakeCentral()
    result = data_browser.fetch_rows("source", "farmers", size=10_000,
                                     central=central, staging=_FakeStaging())
    assert result["size"] == connectors.MAX_PAGE_SIZE
    fetch = [c for c in central.calls if c[0] == "fetch"][0]
    assert fetch[3] == connectors.MAX_PAGE_SIZE


def test_page_below_one_is_clamped(allowlist):
    central = _FakeCentral()
    result = data_browser.fetch_rows("source", "farmers", page=0, size=2,
                                     central=central, staging=_FakeStaging())
    assert result["page"] == 1
    fetch = [c for c in central.calls if c[0] == "fetch"][0]
    assert fetch[4] == 0  # offset


def test_valid_sort_reaches_the_connector(allowlist):
    central = _FakeCentral()
    data_browser.fetch_rows("source", "farmers", sort="full_name", direction="desc",
                            central=central, staging=_FakeStaging())
    fetch = [c for c in central.calls if c[0] == "fetch"][0]
    assert fetch[5] == "full_name" and fetch[6] == "desc"


def test_target_side_reads_staging(allowlist):
    result = data_browser.fetch_rows("target", "irimsv_farmers_staging",
                                     central=_FakeCentral(), staging=_FakeStaging())
    assert result["rows"] == [{"external_reference": "uuid-1"}]
    assert result["total"] == 1


# ---------------------------------------------------------------------------
# Identifier guard (last line of defence in the connectors themselves)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "users; DROP TABLE x",
    "a b",
    "tbl`",
    'tbl"',
    "",
])
def test_safe_identifier_rejects_injection(bad):
    with pytest.raises(ValueError):
        connectors.safe_identifier(bad)


def test_safe_identifier_accepts_plain_names():
    assert connectors.safe_identifier("irimsv_farmers_staging") == "irimsv_farmers_staging"


def test_sort_clause_rejects_bad_direction():
    with pytest.raises(ValueError):
        connectors._sort_clause("col", "asc; DROP")
