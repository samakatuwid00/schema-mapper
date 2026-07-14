"""rebaseline_entity_fingerprints tests — the drift-pause repair path.

Found live 2026-07-14: `deploy_to_lrmis` stores a registry-document target
fingerprint, `ops.monitor` recomputes from live discovery, so one scan paused
every delivering entity — and the original rebaseline filter
(`fingerprint_scope_version < 2`) skipped the v2 rows it needed to repair.
These tests pin the widened eligibility + the re-enable behavior, with the
pipeline and fingerprint computation faked (no live DBs).
"""
import pytest

import src.services.ops as ops


DRIFT_PAUSED_V2 = {
    "id": 7, "source_schema": "irimsv", "source_table": "reservations",
    "target_system": "LRMIS", "status": "paused",
    "paused_reason": "Schema drift detected: source=False, target=True",
    "fingerprint_scope_version": 2, "lrmis_target_tables": ["reservation"],
    "source_fingerprint": "old-sf", "target_fingerprint": "doc-construction-tf",
}


class _FakePipeline:
    def __init__(self, entities):
        self.entities = entities
        self.queries: list[str] = []
        self.executed: list[tuple[str, tuple]] = []

    def _query(self, conn, sql, params=None):
        self.queries.append(sql)
        return [dict(e) for e in self.entities]

    def _execute(self, conn, sql, params=None):
        self.executed.append((sql, params))
        return 1


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass

    def rollback(self):
        pass


class _FakeCentral:
    def connection(self):
        return _FakeConn()

    def close(self):
        pass


@pytest.fixture()
def harness(monkeypatch):
    pipeline = _FakePipeline([DRIFT_PAUSED_V2])
    monkeypatch.setattr(ops, "_pipeline", lambda: pipeline)
    monkeypatch.setattr(ops, "_entity_fingerprints",
                        lambda conn, target, entity: ("live-sf", "live-tf"))
    return pipeline


def test_drift_paused_v2_entities_are_eligible(harness):
    result = ops.rebaseline_entity_fingerprints(
        "tester", apply=False, central=_FakeCentral(), target=object())
    # the widened filter reaches v2 rows paused by the drift monitor
    assert "Schema drift detected:" in harness.queries[0]
    assert result["converted_count"] == 1
    assert result["converted"][0]["entity"] == "reservations"
    assert result["converted"][0]["will_reenable"] is True
    # preview: nothing executed beyond the (rolled back) statements
    assert result["apply"] is False


def test_apply_updates_fingerprints_and_reenables(harness):
    result = ops.rebaseline_entity_fingerprints(
        "tester", apply=True, central=_FakeCentral(), target=object())
    assert result["apply"] is True and result["converted_count"] == 1
    update = next(sql for sql, params in harness.executed
                  if "SET source_fingerprint" in sql)
    params = next(params for sql, params in harness.executed
                  if "SET source_fingerprint" in sql)
    assert params[0] == "live-sf" and params[1] == "live-tf"
    assert params[2] is True                       # was_drift_pause → re-enable
    assert any("entity_control" in sql for sql, _ in harness.executed)
    assert any("fingerprint_rebaseline" in sql for sql, _ in harness.executed)


def test_missing_target_footprint_is_skipped(monkeypatch, harness):
    monkeypatch.setattr(ops, "_entity_fingerprints",
                        lambda conn, target, entity: ("live-sf", None))
    result = ops.rebaseline_entity_fingerprints(
        "tester", apply=True, central=_FakeCentral(), target=object())
    assert result["converted_count"] == 0
    assert result["skipped"][0]["reason"] == "target footprint missing"
    assert not any("SET source_fingerprint" in sql
                   for sql, _ in harness.executed)