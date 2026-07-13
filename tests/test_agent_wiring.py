"""Tests for wiring the agent into deploy + the worker (§8.6)."""
import src.worker as W
from src.agent import MigrationAgent
from src.lrmis_registry import LrmisRegistry
from src.services.lrmis_mapping import coverage_report
from src.services.lrmis_onboarding import deploy_guidance


def _col(t, n, p, key="", extra="", nullable="YES", dtype="int"):
    return {"table_name": t, "column_name": n, "data_type": dtype,
            "is_nullable": nullable, "ordinal_position": p,
            "column_key": key, "extra": extra, "column_default": None}


def test_deploy_guidance_resolves_unmapped_required_column():
    reg = LrmisRegistry.from_discovery([
        _col("school", "id", 1, key="PRI", extra="auto_increment"),
        _col("school", "name", 2, nullable="NO", dtype="varchar(50)"),
    ], [])
    # maps only the auto id, leaving required `name` unmapped -> not deployable
    mappings = [{"source_column": "sid", "target_table": "school",
                 "target_column": "id", "transform": "none"}]
    report = coverage_report(mappings, reg, seeded_tables=set())
    assert not report.ok

    guidance = deploy_guidance(report, reg, MigrationAgent())
    unmapped = [g for g in guidance if g["kind"] == "unmapped_column"]
    assert unmapped
    assert unmapped[0]["table"] == "school" and unmapped[0]["column"] == "name"
    assert unmapped[0]["recommended"] == "auto_suggest"   # exact-name candidate exists


class _Rollbackable:
    def rollback(self):
        pass

    def commit(self):
        pass


def test_worker_heal_annotates_quarantine_on_delivery_error(monkeypatch):
    captured = {}
    monkeypatch.setattr(W, "load_entity_mappings", lambda conn, ent, ts: [{"m": 1}])
    monkeypatch.setattr(W, "deliver_event",
                        lambda *a, **k: {"status": "error", "errors": ["bad row"]})
    monkeypatch.setattr(W, "quarantine",
                        lambda conn, event, reasons, mid: captured.update(reasons=reasons))

    result = {"quarantined": 0}
    target = {"conn": _Rollbackable()}
    event = {"source_entity": "schools", "target_system": "LRMIS"}
    W._deliver_path_b(object(), target, event, result, agent=MigrationAgent())

    assert result["quarantined"] == 1
    assert any(str(r).startswith("agent_heal=") for r in captured["reasons"])
    assert "bad row" in captured["reasons"]     # original reason preserved


def test_worker_without_agent_is_unchanged(monkeypatch):
    captured = {}
    monkeypatch.setattr(W, "load_entity_mappings", lambda conn, ent, ts: [{"m": 1}])
    monkeypatch.setattr(W, "deliver_event",
                        lambda *a, **k: {"status": "error", "errors": ["bad row"]})
    monkeypatch.setattr(W, "quarantine",
                        lambda conn, event, reasons, mid: captured.update(reasons=reasons))
    W._deliver_path_b(object(), {"conn": _Rollbackable()},
                      {"source_entity": "s", "target_system": "LRMIS"},
                      {"quarantined": 0})               # no agent
    assert captured["reasons"] == ["bad row"]           # exactly, no annotation
