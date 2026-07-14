"""Worker delivery (retire-legacy-staging §1): a SINGLE delivery path — every
approved event is delivered directly via `_deliver_path_b` into the real target.
The legacy single-table staging route and the per-entity fork are gone."""
from __future__ import annotations

from contextlib import contextmanager

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


def _events(*names):
    return [{"source_entity": n, "target_system": "LRMIS"} for n in names]


def test_every_event_delivers_via_direct_path(monkeypatch):
    delivered = []
    monkeypatch.setattr(worker, "_deliver_path_b",
                        lambda conn, target, event, result, agent=None:
                        delivered.append(event["source_entity"]))
    monkeypatch.setattr(worker, "claim_events",
                        lambda conn, ts, n: _events("schools", "customer", "authors"))

    opened = {"n": 0}

    def _open(holder):
        opened["n"] += 1
        holder["conn"] = object()

    monkeypatch.setattr(worker, "_open_target", _open)

    result = worker.process_once(central=_Central())
    assert delivered == ["schools", "customer", "authors"]   # all via the one path
    assert result["lrmis"] == 3
    assert opened["n"] == 1                                   # target opened once, lazily


def test_empty_batch_opens_no_target(monkeypatch):
    monkeypatch.setattr(worker, "claim_events", lambda conn, ts, n: [])

    def _no_open(holder):
        raise AssertionError("must not open a target connection for an empty batch")

    monkeypatch.setattr(worker, "_open_target", _no_open)

    result = worker.process_once(central=_Central())
    assert result["claimed"] == 0 and result["lrmis"] == 0
