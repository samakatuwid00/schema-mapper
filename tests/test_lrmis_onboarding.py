"""Phase 6 deploy gating: status + coverage checks before an entity becomes
Path B. DB writes are covered by the live verification; here we exercise the
guards by faking the proposal load."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from src.lrmis_registry import LrmisRegistry, parse_ddl
from src.services import lrmis_onboarding as O
from src.services.common import ValidationError

DDL = """
CREATE TABLE `station` (
  `id` int NOT NULL,
  `geoloc` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB;
"""
REG = LrmisRegistry(parse_ddl(DDL))


class _Central:
    @contextmanager
    def connection(self):
        yield object()

    def close(self):
        pass


def _patch(monkeypatch, proposal, mappings):
    monkeypatch.setattr(O, "_load_proposal_mappings",
                        lambda conn, pid: (proposal, mappings))


def test_rejects_unapproved_proposal(monkeypatch):
    _patch(monkeypatch, {"status": "needs_review", "entity_id": 1,
                         "source_table": "s", "target_system": "LRMIS"}, [])
    with pytest.raises(ValidationError):
        O.deploy_to_lrmis(1, "by", central=_Central(), registry=REG)


def test_rejects_when_no_mappings(monkeypatch):
    _patch(monkeypatch, {"status": "approved", "entity_id": 1,
                         "source_table": "s", "target_system": "LRMIS"}, [])
    with pytest.raises(ValidationError):
        O.deploy_to_lrmis(1, "by", central=_Central(), registry=REG)


def test_rejects_invalid_mapping(monkeypatch):
    # target column that does not exist -> validate_deployment raises
    _patch(monkeypatch, {"status": "approved", "entity_id": 1,
                         "source_table": "s", "target_system": "LRMIS"},
           [{"source_column": "x", "target_table": "station",
             "target_column": "ghost", "transform": "none"}])
    with pytest.raises(ValidationError):
        O.deploy_to_lrmis(1, "by", central=_Central(), registry=REG)


def test_bulk_deploy_continues_past_failures(monkeypatch):
    monkeypatch.setattr(O, "_deployable_proposals",
                        lambda conn, ts: [{"proposal_id": 1, "source_table": "a"},
                                          {"proposal_id": 2, "source_table": "boom"},
                                          {"proposal_id": 3, "source_table": "c"}])

    def fake_deploy(pid, by, central=None, registry=None, target=None):
        if pid == 2:
            raise ValidationError("coverage gap")
        return {"target_tables": ["station"]}

    monkeypatch.setattr(O, "deploy_to_lrmis", fake_deploy)

    out = O.bulk_deploy_to_lrmis("admin", central=_Central(), registry=REG)
    assert out["total"] == 3
    assert out["deployed"] == 2
    assert out["failed"] == 1
    statuses = {r["source_table"]: r["status"] for r in out["results"]}
    assert statuses == {"a": "deployed", "boom": "failed", "c": "deployed"}


# --- deploy-stored fingerprint == monitor-recomputed fingerprint ----------------
# Regression for the 2026-07-14 live incident: deploy used to store a
# registry-document fingerprint while ops.monitor recomputed from live
# discovery, so the first scan after any deploy paused the entity.

STATION_ROWS = [
    {"table_name": "station", "column_name": "id", "data_type": "int",
     "is_nullable": "NO", "column_key": "PRI", "ordinal_position": 1},
    {"table_name": "station", "column_name": "geoloc", "data_type": "varchar",
     "is_nullable": "YES", "column_key": "", "ordinal_position": 2},
]


class _Target:
    def __init__(self, rows=STATION_ROWS):
        self.rows = rows

    def information_schema(self, schema_name=None):
        return [dict(r) for r in self.rows]


class _CommittingCentral:
    """Like _Central but the success path reaches conn.commit()."""

    @contextmanager
    def connection(self):
        class _Conn:
            def commit(self):
                pass

        yield _Conn()

    def close(self):
        pass


class _RecordingPipeline:
    def __init__(self):
        self.executed = []

    def _execute(self, conn, sql, params=None):
        self.executed.append((sql, params))
        return 1

    def _discover_source_schema(self, conn, schema):
        from src.schema_models import Schema
        return Schema(system_name=schema, tables=[])


def _deployable(monkeypatch):
    """Patch a fully-approved, fully-covered proposal + recording sinks."""
    _patch(monkeypatch, {"id": 1, "status": "approved", "entity_id": 7,
                         "source_schema": "irimsv", "source_table": "stations",
                         "target_system": "LRMIS", "entity_status": "reviewed"},
           [{"source_column": "x", "target_table": "station",
             "target_column": "id", "transform": "none"},
            {"source_column": "y", "target_table": "station",
             "target_column": "geoloc", "transform": "none"}])
    pipeline = _RecordingPipeline()
    monkeypatch.setattr(O, "_pipeline", lambda: pipeline)
    monkeypatch.setattr(O, "store_target_tables", lambda conn, eid, m: None)
    return pipeline


def test_deploy_fingerprint_matches_monitor_recomputation(monkeypatch):
    pipeline = _deployable(monkeypatch)
    out = O.deploy_to_lrmis(1, "by", central=_CommittingCentral(), registry=REG,
                            target=_Target())
    assert out["fingerprint"]

    # what ops.monitor recomputes for the same live schema
    import src.services.ops as ops
    monkeypatch.setattr(ops, "_pipeline", lambda: _RecordingPipeline())
    entity = {"source_schema": "irimsv", "source_table": "stations",
              "lrmis_target_tables": ["station"]}
    _, monitor_fp = ops._entity_fingerprints(object(), _Target(), entity)
    assert out["fingerprint"] == monitor_fp

    # the persisted schema_version doc is the live construction
    insert = next((sql, params) for sql, params in pipeline.executed
                  if "schema_version" in sql)
    assert insert[1][2] == out["fingerprint"]
    assert '"integer"' in insert[1][3] and '"string"' in insert[1][3]
    # and the entity row got the same fingerprint
    update = next(params for sql, params in pipeline.executed
                  if "SET status = 'deployed'" in sql)
    assert update[1] == out["fingerprint"]


def test_deploy_with_absent_target_tables_stores_no_contract(monkeypatch):
    pipeline = _deployable(monkeypatch)
    out = O.deploy_to_lrmis(1, "by", central=_CommittingCentral(), registry=REG,
                            target=_Target(rows=[]))
    assert out["fingerprint"] is None
    assert out["target_contract_empty"] is True
    assert not any("schema_version" in sql for sql, _ in pipeline.executed)


def test_bulk_propose_continues_past_failures(monkeypatch):
    monkeypatch.setattr(O, "_tables_to_repropose", lambda conn, ss, ts: ["a", "boom", "c"])

    def fake_propose(ss, table, ts, central=None):
        if table == "boom":
            raise RuntimeError("gemini exploded")
        return {"proposal_id": 9, "auto_approved": 1, "needs_review": 0, "gemini_error": None}

    import src.services.onboarding as ON
    monkeypatch.setattr(ON, "propose", fake_propose)

    out = O.bulk_propose_lrmis("admin", central=_Central())
    assert out["total"] == 3
    assert out["proposed"] == 2
    assert out["failed"] == 1
    statuses = {r["source_table"]: r["status"] for r in out["results"]}
    assert statuses == {"a": "proposed", "boom": "failed", "c": "proposed"}
