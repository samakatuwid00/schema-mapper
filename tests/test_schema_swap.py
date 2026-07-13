"""Tests for schema-swap diff + affected-entity detection (§0.4).

Pure-function tests: no live target and no central DB required.
"""
from src.lrmis_registry import LrmisRegistry
from src.mapping_engine import FieldMapping
from src.schema_models import Column, Table
from src.services.schema_swap import (
    diff_registries, changed_table_set, affected_entities,
    build_target_schema, remap_entity, apply,
)


def _col(table, name, pos, dtype="int", nullable="YES", key="", extra=""):
    return {"table_name": table, "column_name": name, "data_type": dtype,
            "is_nullable": nullable, "ordinal_position": pos,
            "column_key": key, "extra": extra, "column_default": None}


# "old" approved target
OLD = LrmisRegistry.from_discovery([
    _col("station", "id", 1, key="PRI"),
    _col("school", "id", 1, key="PRI", extra="auto_increment"),
    _col("school", "name", 2, dtype="varchar(50)", nullable="NO"),
    _col("legacy", "id", 1, key="PRI"),
])

# "new" discovered target: legacy dropped, author added, school.name changed type
NEW = LrmisRegistry.from_discovery([
    _col("station", "id", 1, key="PRI"),
    _col("school", "id", 1, key="PRI", extra="auto_increment"),
    _col("school", "name", 2, dtype="varchar(120)", nullable="NO"),
    _col("author", "id", 1, key="PRI", extra="auto_increment"),
])


def test_diff_detects_added_removed_and_changed():
    diff = diff_registries(OLD, NEW)
    assert diff["added_tables"] == ["author"]
    assert diff["removed_tables"] == ["legacy"]
    assert "school" in diff["changed_tables"]
    assert diff["changed_tables"]["school"]["changed_columns"] == ["name"]
    # station unchanged -> not reported
    assert "station" not in diff["changed_tables"]


def test_changed_table_set_unions_all_changes():
    diff = diff_registries(OLD, NEW)
    assert changed_table_set(diff) == {"author", "legacy", "school"}


def test_identical_registries_have_empty_diff():
    diff = diff_registries(OLD, OLD)
    assert diff == {"added_tables": [], "removed_tables": [], "changed_tables": {}}


def test_affected_entities_only_those_touching_changed_tables():
    diff = diff_registries(OLD, NEW)
    entities = [
        {"source_schema": "irimsv", "source_table": "schools",
         "lrmis_target_tables": ["school", "station"]},   # school changed -> affected
        {"source_schema": "irimsv", "source_table": "unchanged",
         "lrmis_target_tables": ["station"]},              # station unchanged -> not affected
        {"source_schema": "irimsv", "source_table": "authors",
         "lrmis_target_tables": ["author"]},               # author added -> affected
    ]
    affected = affected_entities(diff, entities)
    names = {a["source_table"] for a in affected}
    assert names == {"schools", "authors"}
    schools = next(a for a in affected if a["source_table"] == "schools")
    assert schools["affected_tables"] == ["school"]


def test_affected_entities_parses_json_string_footprint():
    diff = diff_registries(OLD, NEW)
    entities = [{"source_table": "schools", "lrmis_target_tables": '["school"]'}]
    affected = affected_entities(diff, entities)
    assert affected and affected[0]["affected_tables"] == ["school"]


# --- AI re-map + confirmed apply (§0.5) ------------------------------------

def _src_table(name="schools", cols=("id", "name")):
    return Table(name=name, columns=[
        Column(name=c, data_type="int", nullable=True, is_primary_key=(c == "id"))
        for c in cols])


def _fake_propose(mappings):
    def _propose(source_table, target_schema, client=None):
        return mappings
    return _propose


class _FakeAdapter:
    engine_type = "postgres"

    def __init__(self, registry):
        self._registry = registry

    def discover_registry(self):
        return self._registry


ENTITIES = [
    {"source_schema": "irimsv", "source_table": "schools",
     "lrmis_target_tables": ["school"]},   # school changed -> affected
    {"source_schema": "irimsv", "source_table": "other",
     "lrmis_target_tables": ["station"]},  # station unchanged -> not affected
]


def test_remap_entity_flags_low_confidence():
    target = build_target_schema(NEW, ["school"])
    fm = [FieldMapping("id", "school", "id", 0.95, "none", "pk"),
          FieldMapping("name", "school", "name", 0.40, "none", "weak")]
    result = remap_entity(_src_table(), target, threshold=0.7, propose=_fake_propose(fm))
    assert result["auto_ok"] is False
    assert [n["source_column"] for n in result["needs_review"]] == ["name"]


def test_remap_entity_all_high_confidence_ok():
    target = build_target_schema(NEW, ["school"])
    fm = [FieldMapping("id", "school", "id", 0.9, "none", ""),
          FieldMapping("name", "school", "name", 0.85, "none", "")]
    result = remap_entity(_src_table(), target, threshold=0.7, propose=_fake_propose(fm))
    assert result["auto_ok"] is True and result["needs_review"] == []


def _apply(propose_mappings, *, force=False, sink=None):
    sink = sink if sink is not None else {}

    def persist(central, affected, remaps, new_registry, source_tables, by):
        sink["persisted"] = [a["source_table"] for a in affected]
        return sink["persisted"]

    def recreate(adapter, **kw):
        sink["recreated"] = True
        return {"ok": True}

    def redeliver(adapter, entities, **kw):
        sink["delivered"] = [e["source_table"] for e in entities]
        return {"status": "applied"}

    result = apply(
        target_adapter=_FakeAdapter(NEW), old_registry=OLD,
        fetch_entities=lambda central, ts: ENTITIES,
        discover_source=lambda central, affected: {"schools": _src_table()},
        propose=_fake_propose(propose_mappings),
        persist=persist, recreate=recreate, redeliver=redeliver, force=force)
    return result, sink


def test_apply_blocks_on_low_confidence_and_changes_nothing():
    fm = [FieldMapping("name", "school", "name", 0.3, "none", "weak")]
    result, sink = _apply(fm, force=False)
    assert result["status"] == "blocked_on_review"
    assert result["blocked"] == ["schools"]
    # nothing was persisted and no destructive step ran
    assert "persisted" not in sink
    assert "recreated" not in sink and "delivered" not in sink


def test_apply_force_recreates_and_redelivers_all_deployed():
    fm = [FieldMapping("name", "school", "name", 0.3, "none", "weak")]
    result, sink = _apply(fm, force=True)
    assert result["status"] == "applied"
    assert sink.get("recreated") is True
    # recreate empties the target, so ALL deployed entities are re-delivered
    assert sink["delivered"] == ["schools", "other"]


def test_apply_high_confidence_proceeds_without_force():
    fm = [FieldMapping("name", "school", "name", 0.95, "none", "")]
    result, sink = _apply(fm, force=False)
    assert result["status"] == "applied"
    assert sink.get("recreated") is True
    assert sink.get("persisted") == ["schools"]


def test_apply_persists_before_recreate_and_redeliver():
    order = []
    fm = [FieldMapping("name", "school", "name", 0.95, "none", "")]
    apply(
        target_adapter=_FakeAdapter(NEW), old_registry=OLD,
        fetch_entities=lambda central, ts: ENTITIES,
        discover_source=lambda central, affected: {"schools": _src_table()},
        propose=_fake_propose(fm),
        persist=lambda *a, **k: order.append("persist") or [],
        recreate=lambda a, **k: order.append("recreate") or {},
        redeliver=lambda a, e, **k: (order.append("redeliver"), {"status": "applied"})[1])
    assert order == ["persist", "recreate", "redeliver"]


def test_field_review_rows_skips_unmapped():
    from src.services.schema_swap import _field_review_rows
    fms = [FieldMapping("a", "school", "name", 0.9, "none", "ok"),
           FieldMapping("b", None, None, 0.0, "none", "no match")]
    rows = _field_review_rows(fms)
    assert len(rows) == 1
    assert rows[0]["suggested_target_table"] == "school"
    assert rows[0]["status"] == "accepted"


def test_persist_remap_inserts_proposal_then_reviews():
    from src.services.schema_swap import persist_remap

    calls = []

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            calls.append(sql)

        def fetchone(self):
            return (42,)

    class _Conn:
        def cursor(self):
            return _Cur()

    fms = [FieldMapping("a", "school", "name", 0.9, "none", "ok")]
    pid = persist_remap(_Conn(), entity_id=7, source_fingerprint="sf",
                        target_fingerprint="tf", mappings=fms, by="admin")
    assert pid == 42
    assert "onboarding_proposal" in calls[0]
    assert "onboarding_field_review" in calls[1]
