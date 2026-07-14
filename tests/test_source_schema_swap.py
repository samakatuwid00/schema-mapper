"""Source-side schema-swap tests (source-schema-swap-and-disaster-recovery §2).

Pure-function + seam-injected tests: no live source database and no central DB.
Covers diff detection, remap trigger thresholds, human-gate behavior, and the
no-DDL-against-source guard (design D2).
"""
from src.lrmis_registry import LrmisRegistry
from src.mapping_engine import FieldMapping
from src.schema_models import Column, Schema, Table
from src.services.schema_swap import (
    affected_source_entities, apply, diff_source_table, dry_run,
)


def _table(name, cols):
    """cols: (name, data_type, nullable) triples."""
    return Table(name=name, columns=[
        Column(name=n, data_type=t, nullable=nu) for n, t, nu in cols])


def _col(table, name, pos, dtype="int", nullable="YES", key="", extra=""):
    return {"table_name": table, "column_name": name, "data_type": dtype,
            "is_nullable": nullable, "ordinal_position": pos,
            "column_key": key, "extra": extra, "column_default": None}


# Approved target registry (never changes in a source-side swap).
TARGET_REGISTRY = LrmisRegistry.from_discovery([
    _col("school", "id", 1, key="PRI"),
    _col("school", "name", 2, dtype="varchar(120)", nullable="NO"),
    _col("district", "id", 1, key="PRI"),
    _col("district", "name", 2, dtype="varchar(120)"),
])

# The restructured source: schools.name renamed to school_name (+ region added),
# districts unchanged, retired_table gone entirely.
NEW_SOURCE = Schema(system_name="IRIMSV", tables=[
    _table("schools", [("id", "integer", False), ("school_name", "string", False),
                       ("region", "string", True)]),
    _table("districts", [("id", "integer", False), ("name", "string", True)]),
])

OLD_SCHOOLS = _table("schools", [("id", "integer", False), ("name", "string", False)])
OLD_DISTRICTS = _table("districts", [("id", "integer", False), ("name", "string", True)])

ENTITIES = [
    {"id": 1, "source_schema": "irimsv", "source_table": "schools",
     "lrmis_target_tables": ["school"]},
    {"id": 2, "source_schema": "irimsv", "source_table": "districts",
     "lrmis_target_tables": ["district"]},
]

CONTRACTS = {
    "schools": {"table": OLD_SCHOOLS, "columns": ["id", "name"]},
    "districts": {"table": OLD_DISTRICTS, "columns": ["id", "name"]},
}


# --- diff detection ---------------------------------------------------------

def test_diff_source_table_detects_added_removed_changed():
    old = _table("t", [("id", "integer", False), ("name", "string", False),
                       ("kept", "string", True)])
    new = _table("t", [("id", "integer", False), ("kept", "text", True),
                       ("fresh", "string", True)])
    diff = diff_source_table(old, new)
    assert diff["missing_table"] is False
    assert diff["added_columns"] == ["fresh"]
    assert diff["removed_columns"] == ["name"]
    assert diff["changed_columns"] == ["kept"]   # string -> text


def test_diff_source_table_nullability_change_is_a_change():
    old = _table("t", [("id", "integer", False)])
    new = _table("t", [("id", "integer", True)])
    assert diff_source_table(old, new)["changed_columns"] == ["id"]


def test_diff_source_table_missing_table():
    diff = diff_source_table(OLD_SCHOOLS, None)
    assert diff["missing_table"] is True
    assert diff["removed_columns"] == ["id", "name"]


def test_affected_only_entities_with_breaking_source_changes():
    affected, details = affected_source_entities(NEW_SOURCE, ENTITIES, CONTRACTS)
    assert [a["source_table"] for a in affected] == ["schools"]
    assert details["schools"]["removed_columns"] == ["name"]
    assert details["schools"]["added_columns"] == ["region", "school_name"]
    assert details["districts"] == {
        "missing_table": False, "added_columns": [], "removed_columns": [],
        "changed_columns": [], "contract_source": "document"}
    # the affected entry keeps its existing target footprint for the re-map
    assert affected[0]["target_tables"] == ["school"]
    assert affected[0]["affected_tables"] == ["schools"]


def test_added_only_change_is_reported_but_not_affected():
    source = Schema(system_name="IRIMSV", tables=[
        _table("districts", [("id", "integer", False), ("name", "string", True),
                             ("bonus", "string", True)])])
    entities = [ENTITIES[1]]
    affected, details = affected_source_entities(
        source, entities, {"districts": CONTRACTS["districts"]})
    assert affected == []
    assert details["districts"]["added_columns"] == ["bonus"]


def test_missing_source_table_marks_entity_affected():
    source = Schema(system_name="IRIMSV", tables=[])
    affected, details = affected_source_entities(
        source, [ENTITIES[0]], {"schools": CONTRACTS["schools"]})
    assert [a["source_table"] for a in affected] == ["schools"]
    assert details["schools"]["missing_table"] is True


def test_fallback_contract_detects_removals_but_not_retypes():
    """Without a captured document, the approved column names still catch
    removed columns; a pure retype is invisible (types are mirrored)."""
    contracts = {"schools": {"table": None, "columns": ["id", "name"]}}
    affected, details = affected_source_entities(NEW_SOURCE, [ENTITIES[0]], contracts)
    assert details["schools"]["contract_source"] == "approved_columns"
    assert details["schools"]["removed_columns"] == ["name"]
    assert [a["source_table"] for a in affected] == ["schools"]

    retyped_only = Schema(system_name="IRIMSV", tables=[
        _table("schools", [("id", "text", False), ("name", "string", False)])])
    affected, details = affected_source_entities(retyped_only, [ENTITIES[0]], contracts)
    assert affected == []          # limitation, by design: needs a captured doc
    assert details["schools"]["changed_columns"] == []


def test_retype_detected_with_captured_document():
    retyped_only = Schema(system_name="IRIMSV", tables=[
        _table("schools", [("id", "text", False), ("name", "string", False)])])
    affected, details = affected_source_entities(
        retyped_only, [ENTITIES[0]], {"schools": CONTRACTS["schools"]})
    assert [a["source_table"] for a in affected] == ["schools"]
    assert details["schools"]["changed_columns"] == ["id"]


# --- dry-run + apply through the seams --------------------------------------

class _SpySourceAdapter:
    """Exposes ONLY read-discovery. If any code path tried to execute SQL or
    DDL against the source, there is nothing here to call — the D2 guard."""
    engine_type = "postgres"
    schema = "irimsv"

    def __init__(self, schema_obj):
        self._schema = schema_obj
        self.calls = []

    def discover_schema(self):
        self.calls.append("discover_schema")
        return self._schema


def _fake_propose(mappings):
    def _propose(source_table, target_schema, client=None):
        return mappings
    return _propose


def test_dry_run_source_reports_affected_and_changes_nothing():
    adapter = _SpySourceAdapter(NEW_SOURCE)
    result = dry_run(side="source", source_adapter=adapter,
                     fetch_entities=lambda central, ts: ENTITIES,
                     fetch_source_contracts=lambda central, ts: CONTRACTS)
    assert result["side"] == "source"
    assert result["would_remap"] == ["schools"]
    assert result["deployed_entities"] == 2
    assert adapter.calls == ["discover_schema"]


def test_dry_run_source_unchanged_source_reports_zero_affected():
    unchanged = Schema(system_name="IRIMSV", tables=[OLD_SCHOOLS, OLD_DISTRICTS])
    result = dry_run(side="source", source_adapter=_SpySourceAdapter(unchanged),
                     fetch_entities=lambda central, ts: ENTITIES,
                     fetch_source_contracts=lambda central, ts: CONTRACTS)
    assert result["affected_entities"] == []
    assert result["would_remap"] == []


def _apply_source(propose_mappings, *, force=False):
    sink = {}
    adapter = _SpySourceAdapter(NEW_SOURCE)

    def persist(central, affected, remaps, target_registry, source_tables, by):
        sink["persisted"] = [a["source_table"] for a in affected]
        sink["registry"] = target_registry
        return [{"source_table": s} for s in sink["persisted"]]

    result = apply(
        side="source", source_adapter=adapter, old_registry=TARGET_REGISTRY,
        fetch_entities=lambda central, ts: ENTITIES,
        fetch_source_contracts=lambda central, ts: CONTRACTS,
        propose=_fake_propose(propose_mappings), persist=persist, force=force)
    return result, sink, adapter


def test_apply_source_blocks_on_low_confidence_and_persists_nothing():
    fm = [FieldMapping("school_name", "school", "name", 0.3, "none", "weak")]
    result, sink, adapter = _apply_source(fm)
    assert result["status"] == "blocked_on_review"
    assert result["blocked"] == ["schools"]
    assert "persisted" not in sink
    assert adapter.calls == ["discover_schema"]


def test_apply_source_high_confidence_persists_and_resumes():
    fm = [FieldMapping("id", "school", "id", 0.95, "none", "pk"),
          FieldMapping("school_name", "school", "name", 0.9, "none", "renamed")]
    result, sink, adapter = _apply_source(fm)
    assert result["status"] == "applied"
    assert sink["persisted"] == ["schools"]
    assert sink["registry"] is TARGET_REGISTRY    # approved registry, not rediscovered
    # D2: a source swap has no recreate and no redeliver step
    assert "recreate" not in result and "redeliver" not in result
    assert adapter.calls == ["discover_schema"]


def test_apply_source_force_overrides_the_gate():
    fm = [FieldMapping("school_name", "school", "name", 0.3, "none", "weak")]
    result, sink, _ = _apply_source(fm, force=True)
    assert result["status"] == "applied"
    assert sink["persisted"] == ["schools"]


def test_apply_source_dropped_table_cannot_auto_remap():
    """An entity whose source table vanished is never silently remapped —
    remap_affected flags it and the gate blocks."""
    gone = Schema(system_name="IRIMSV", tables=[OLD_DISTRICTS])
    adapter = _SpySourceAdapter(gone)
    result = apply(
        side="source", source_adapter=adapter, old_registry=TARGET_REGISTRY,
        fetch_entities=lambda central, ts: ENTITIES,
        fetch_source_contracts=lambda central, ts: CONTRACTS,
        propose=_fake_propose([]), persist=lambda *a: [])
    assert result["status"] == "blocked_on_review"
    assert result["blocked"] == ["schools"]


def test_target_side_results_still_shape_compatible():
    """The side param defaults to target and existing callers see the same
    result shape (plus an explicit side key)."""
    import src.services.schema_swap as ss
    OLD = LrmisRegistry.from_discovery([_col("school", "id", 1, key="PRI")])

    class _FakeTargetAdapter:
        engine_type = "postgres"

        def discover_registry(self):
            return OLD

    result = ss.dry_run(target_adapter=_FakeTargetAdapter(), old_registry=OLD,
                        fetch_entities=lambda central, ts: [])
    assert result["side"] == "target"
    assert result["affected_entities"] == []
