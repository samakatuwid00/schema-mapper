"""Path B delivery (Phases 4-5): value grouping, transforms, deliver/refresh.

The writer and connections are faked so no database is needed; the delivery
logic and its transform/deactivate/refresh behavior are what is exercised.
"""
from __future__ import annotations

from datetime import date

import pytest

from src import lrmis_delivery as D


def mp(src, table, col, transform="none"):
    return {"source_column": src, "target_table": table,
            "target_column": col, "transform": transform}


# ---------------------------------------------------------------------------
# build_values_by_table
# ---------------------------------------------------------------------------

def test_groups_values_by_target_table():
    row = {"school_name": "Naga ES", "addr": "Main St", "opened": date(2020, 1, 2)}
    mappings = [mp("school_name", "station", "geoloc"),
                mp("addr", "station_address", "street"),
                mp("opened", "beis", "date_established", "cast:date->datetime")]
    values, errors = D.build_values_by_table(row, mappings)
    assert errors == []
    assert values["station"] == {"geoloc": "Naga ES"}
    assert values["station_address"] == {"street": "Main St"}
    assert values["beis"]["date_established"].isoformat().startswith("2020-01-02")


def test_missing_source_column_is_skipped():
    values, errors = D.build_values_by_table({"a": 1}, [mp("b", "station", "geoloc")])
    assert values == {} and errors == []


def test_rejected_mapping_without_target_is_skipped():
    values, _ = D.build_values_by_table(
        {"a": 1}, [{"source_column": "a", "target_table": None, "target_column": None}])
    assert values == {}


def test_transform_failure_is_collected_not_raised():
    values, errors = D.build_values_by_table(
        {"n": "notanumber"}, [mp("n", "beis", "count", "cast:str->int")])
    assert values == {}
    assert errors and "transform failed" in errors[0]


def test_skips_writer_managed_pk_and_fk_columns():
    from src.lrmis_registry import LrmisRegistry, parse_ddl
    reg = LrmisRegistry(parse_ddl(
        "CREATE TABLE `acquisition` (\n"
        "  `id` int NOT NULL AUTO_INCREMENT,\n"
        "  PRIMARY KEY (`id`)\n"
        ") ENGINE=InnoDB;\n"
        "CREATE TABLE `masterlist` (\n"
        "  `id` int NOT NULL AUTO_INCREMENT,\n"
        "  `qty` int,\n"
        "  `acquisition_id` int,\n"
        "  PRIMARY KEY (`id`),\n"
        "  FOREIGN KEY (`acquisition_id`) REFERENCES `acquisition` (`id`)\n"
        ") ENGINE=InnoDB;"))
    row = {"src_qty": 5, "src_acq": "a-uuid", "src_id": "another-uuid"}
    mappings = [
        mp("src_qty", "masterlist", "qty"),
        mp("src_acq", "masterlist", "acquisition_id"),  # FK -> writer-managed, skipped
        mp("src_id", "masterlist", "id"),               # AUTO_INCREMENT PK -> skipped
    ]
    values, errors = D.build_values_by_table(row, mappings, reg)
    assert errors == []
    assert values == {"masterlist": {"qty": 5}}


def test_over_length_string_is_coerced_to_target_column_width():
    from src.lrmis_registry import LrmisRegistry, parse_ddl
    reg = LrmisRegistry(parse_ddl(
        "CREATE TABLE `station_address` (\n"
        "  `id` int NOT NULL AUTO_INCREMENT,\n"
        "  `street` varchar(10),\n"
        "  `note` text,\n"
        "  PRIMARY KEY (`id`)\n"
        ") ENGINE=InnoDB;"))
    values, errors = D.build_values_by_table(
        {"addr": "0123456789ABCDEF", "long_note": "x" * 500},
        [mp("addr", "station_address", "street"),       # 16 chars -> varchar(10)
         mp("long_note", "station_address", "note")],   # TEXT: unbounded, untouched
        reg)
    assert errors == []
    assert values["station_address"]["street"] == "0123456789"   # truncated to varchar(10)
    assert values["station_address"]["note"] == "x" * 500        # TEXT untouched


def test_under_width_string_is_left_intact():
    from src.lrmis_registry import LrmisRegistry, parse_ddl
    reg = LrmisRegistry(parse_ddl(
        "CREATE TABLE `station_address` (\n"
        "  `id` int NOT NULL AUTO_INCREMENT,\n"
        "  `street` varchar(75),\n"
        "  PRIMARY KEY (`id`)\n"
        ") ENGINE=InnoDB;"))
    values, errors = D.build_values_by_table(
        {"addr": "Main St"}, [mp("addr", "station_address", "street")], reg)
    assert errors == []
    assert values["station_address"]["street"] == "Main St"


# ---------------------------------------------------------------------------
# resolve_cross_entity_fks
# ---------------------------------------------------------------------------

def _fk_registry():
    from src.lrmis_registry import LrmisRegistry, parse_ddl
    return LrmisRegistry(parse_ddl(
        "CREATE TABLE `user_type` (\n"
        "  `id` int NOT NULL AUTO_INCREMENT,\n"
        "  `name` varchar(50),\n"
        "  PRIMARY KEY (`id`)\n"
        ") ENGINE=InnoDB;\n"
        "CREATE TABLE `station` (\n"
        "  `id` int NOT NULL AUTO_INCREMENT,\n"
        "  PRIMARY KEY (`id`)\n"
        ") ENGINE=InnoDB;\n"
        "CREATE TABLE `user` (\n"
        "  `id` int NOT NULL AUTO_INCREMENT,\n"
        "  `name` varchar(50),\n"
        "  `usertype_id` int,\n"
        "  `station_id` int,\n"
        "  PRIMARY KEY (`id`),\n"
        "  FOREIGN KEY (`usertype_id`) REFERENCES `user_type` (`id`),\n"
        "  FOREIGN KEY (`station_id`) REFERENCES `station` (`id`)\n"
        ") ENGINE=InnoDB;"))


def test_resolve_cross_entity_fk_injects_hit_and_reports_miss(monkeypatch):
    reg = _fk_registry()
    monkeypatch.setattr(D, "_candidate_entities_for", lambda *a, **k: ["ref_entity"])
    # user_type resolves to 7; station has no crosswalk row -> None
    monkeypatch.setattr(D, "_crosswalk_target_id",
                        lambda conn, ent, key, tsys, tbl: 7 if tbl == "user_type" else None)
    monkeypatch.setattr(D, "_target_row_exists", lambda *a, **k: True)

    row = {"name": "Ana", "usertype_id": "ut-uuid", "station_id": "st-uuid"}
    values = {"user": {"name": "Ana"}}          # FKs were dropped by build_values_by_table
    mappings = [mp("name", "user", "name"),
                mp("usertype_id", "user", "usertype_id"),
                mp("station_id", "user", "station_id")]

    unresolved = D.resolve_cross_entity_fks(object(), row, mappings, values, reg,
                                            target_conn=object())

    assert values["user"]["usertype_id"] == 7      # resolved FK injected
    assert "station_id" not in values["user"]      # unresolved -> left null
    assert unresolved == [{"target_table": "user", "target_column": "station_id",
                           "source_column": "station_id", "source_value": "st-uuid"}]


def test_resolve_treats_stale_crosswalk_id_as_unresolved(monkeypatch):
    reg = _fk_registry()
    monkeypatch.setattr(D, "_candidate_entities_for", lambda *a, **k: ["ref_entity"])
    monkeypatch.setattr(D, "_crosswalk_target_id", lambda *a, **k: 999)  # crosswalk has it
    monkeypatch.setattr(D, "_target_row_exists", lambda *a, **k: False)  # but row is gone

    row = {"usertype_id": "ut-uuid"}
    values = {"user": {}}
    mappings = [mp("usertype_id", "user", "usertype_id")]

    unresolved = D.resolve_cross_entity_fks(object(), row, mappings, values, reg,
                                            target_conn=object())

    assert "usertype_id" not in values["user"]     # stale id never injected
    assert len(unresolved) == 1 and unresolved[0]["target_column"] == "usertype_id"


def test_resolve_ignores_null_source_and_non_fk_columns(monkeypatch):
    reg = _fk_registry()
    called = {"n": 0}
    monkeypatch.setattr(D, "_candidate_entities_for",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or [])
    row = {"name": "Ana", "usertype_id": None}     # name is not an FK; usertype_id is null
    values = {"user": {"name": "Ana"}}
    mappings = [mp("name", "user", "name"), mp("usertype_id", "user", "usertype_id")]

    unresolved = D.resolve_cross_entity_fks(object(), row, mappings, values, reg,
                                            target_conn=object())
    assert unresolved == []
    assert called["n"] == 0                         # no crosswalk probing attempted


# ---------------------------------------------------------------------------
# deliver_event
# ---------------------------------------------------------------------------

class FakeTargetConn:
    def __init__(self):
        self.audit = []

    def cursor(self):
        outer = self

        class _C:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def execute(self_, sql, params=()):
                if "delivery_audit" in sql:
                    outer.audit.append(params)
        return _C()


def _event(op="insert"):
    return {"event_id": "e1", "external_reference": "uuid-1", "operation": op,
            "source_system": "IRIMSV_REGION_V", "source_updated_at": None,
            "mapping_version": 3, "payload_checksum": "abc", "attempts": 0,
            "payload": {"school_name": "Naga ES", "addr": "Main St"}}


def test_deliver_event_writes_and_audits(monkeypatch):
    captured = {}

    def fake_write(target_conn, central_conn, **kw):
        captured.update(kw)
        return {"station": 10_000_001, "station_address": 5}

    monkeypatch.setattr(D, "write_source_row", fake_write)
    target = FakeTargetConn()
    mappings = [mp("school_name", "station", "geoloc"),
                mp("addr", "station_address", "street")]

    out = D.deliver_event(target, object(), entity_name="schools", event=_event(),
                          mappings=mappings, registry=object())

    assert out["status"] == "delivered"
    assert out["active"] is True
    assert captured["source_entity"] == "schools"
    assert set(captured["values_by_table"]) == {"station", "station_address"}
    assert len(target.audit) == 1
    assert target.audit[0][-2] == 1        # active flag = 1


def test_deactivate_marks_audit_inactive_but_still_writes(monkeypatch):
    monkeypatch.setattr(D, "write_source_row", lambda *a, **k: {"station": 1})
    target = FakeTargetConn()
    out = D.deliver_event(target, object(), entity_name="schools",
                          event=_event(op="deactivate"),
                          mappings=[mp("school_name", "station", "geoloc")], registry=object())
    assert out["active"] is False
    assert target.audit[0][-2] == 0        # active flag = 0


def test_deliver_event_returns_error_on_transform_failure(monkeypatch):
    monkeypatch.setattr(D, "write_source_row",
                        lambda *a, **k: pytest.fail("must not write on error"))
    target = FakeTargetConn()
    bad = _event()
    bad["payload"] = {"n": "x"}
    out = D.deliver_event(target, object(), entity_name="schools", event=bad,
                          mappings=[mp("n", "beis", "count", "cast:str->int")],
                          registry=object())
    assert out["status"] == "error"


def test_deliver_event_errors_when_no_target_values(monkeypatch):
    monkeypatch.setattr(D, "write_source_row",
                        lambda *a, **k: pytest.fail("must not write"))
    out = D.deliver_event(FakeTargetConn(), object(), entity_name="schools",
                          event=_event(), mappings=[mp("absent", "station", "geoloc")],
                          registry=object())
    assert out["status"] == "error"


# ---------------------------------------------------------------------------
# refresh_entity
# ---------------------------------------------------------------------------

def test_refresh_deletes_then_rewrites(monkeypatch):
    calls = {"delete": 0, "write": 0}

    def fake_delete(*a, **k):
        calls["delete"] += 1
        return {"beis": 3}

    def fake_write(*a, **k):
        calls["write"] += 1
        return {"station": 1}

    monkeypatch.setattr(D, "delete_entity_rows", fake_delete)
    monkeypatch.setattr(D, "write_source_row", fake_write)

    rows = [{"school_name": "A"}, {"school_name": "B"}]
    out = D.refresh_entity(object(), object(), entity_name="schools",
                           mappings=[mp("school_name", "station", "geoloc")],
                           source_rows=rows,
                           external_reference_of=lambda r: f"uuid-{r['school_name']}",
                           registry=object())
    assert calls["delete"] == 1              # delete happens once, before writes
    assert out["written"] == 2
    assert out["deleted"] == {"beis": 3}


def test_refresh_records_failures_and_continues(monkeypatch):
    monkeypatch.setattr(D, "delete_entity_rows", lambda *a, **k: {})
    monkeypatch.setattr(D, "write_source_row", lambda *a, **k: {"station": 1})
    rows = [{"school_name": "ok"}, {"other": "x"}]   # 2nd has no mapped source col
    out = D.refresh_entity(object(), object(), entity_name="schools",
                           mappings=[mp("school_name", "station", "geoloc")],
                           source_rows=rows,
                           external_reference_of=lambda r: "u", registry=object())
    assert out["written"] == 1
    assert len(out["failed"]) == 1
