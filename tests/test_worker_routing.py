"""Worker routing (Phase 5): Path B entities go to lrmis_target; every other
entity takes the untouched legacy staging path, and a pure-legacy batch never
opens a target connection."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from src import worker


class _Conn:
    def cursor(self, *a, **k):
        raise AssertionError("routing test should not query through the fake conn")

    def commit(self):
        pass


class _Central:
    @contextmanager
    def connection(self):
        yield _Conn()

    def close(self):
        pass


@pytest.fixture()
def routed(monkeypatch):
    legacy, path_b = [], []
    monkeypatch.setattr(worker, "_deliver_legacy",
                        lambda conn, staging, event, result: legacy.append(event["source_entity"]))
    monkeypatch.setattr(worker, "_deliver_path_b",
                        lambda conn, target, event, result, agent=None: path_b.append(event["source_entity"]))
    return legacy, path_b


def _events(*names):
    return [{"source_entity": n, "target_system": "LRMIS"} for n in names]


def test_legacy_entities_take_legacy_path_and_open_no_target(monkeypatch, routed):
    legacy, path_b = routed
    monkeypatch.setattr(worker, "claim_events", lambda conn, ts, n: _events("customer", "authors"))
    monkeypatch.setattr(worker, "is_path_b_entity", lambda conn, e, ts: False)

    def _no_target():
        raise AssertionError("must not open a target connection for a legacy-only batch")
    monkeypatch.setattr(worker.MySQLStagingConnector, "for_target", staticmethod(_no_target))

    result = worker.process_once(central=_Central(), staging=object())
    assert legacy == ["customer", "authors"]
    assert path_b == []
    assert result["lrmis"] == 0


def test_path_b_entities_route_to_target(monkeypatch, routed):
    legacy, path_b = routed
    monkeypatch.setattr(worker, "claim_events", lambda conn, ts, n: _events("schools", "customer"))
    monkeypatch.setattr(worker, "is_path_b_entity",
                        lambda conn, e, ts: e == "schools")

    class _FakeTargetConnector:
        @contextmanager
        def connection(self_):
            yield object()

    monkeypatch.setattr(worker.MySQLStagingConnector, "for_target",
                        staticmethod(lambda: _FakeTargetConnector()))

    result = worker.process_once(central=_Central(), staging=object())
    assert path_b == ["schools"]
    assert legacy == ["customer"]
    assert result["lrmis"] == 1
