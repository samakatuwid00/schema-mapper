"""onboard_bulk: conservative bucketing, non-destructiveness, resilience.

The service functions it composes are stubbed, so what is exercised is the
orchestration contract: which bucket a table lands in, that a failure does not
abort the batch, and that deploy/backfill are never reached for uncertain
tables.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from src.services import onboarding


class _FakeCentral:
    @contextmanager
    def connection(self):
        yield object()

    def close(self):
        pass


class _Recorder:
    """Stand-in for the service functions, recording what bulk actually calls."""

    def __init__(self, proposals: dict, entity_status: dict | None = None,
                 raise_on: dict | None = None):
        self.proposals = proposals
        self.entity_status = entity_status or {}
        self.raise_on = raise_on or {}
        self.deployed: list[int] = []
        self.backfilled: list[str] = []
        self.discovered = 0

    def query(self, conn, sql, params=None):
        # onboard_bulk's only direct query is the already-deployed lookup.
        table = params[1]
        status = self.entity_status.get(table)
        return [{"status": status}] if status else []

    def discover(self, schema, target, central=None):
        self.discovered += 1
        return {}

    def propose(self, schema, table, target, central=None):
        if table in self.raise_on:
            raise RuntimeError(self.raise_on[table])
        return self.proposals[table]

    def deploy(self, proposal_id, actor, central=None, staging=None):
        self.deployed.append(proposal_id)
        return {"staging_table": f"irimsv_t{proposal_id}_staging", "mappings": 4}

    def backfill(self, table, central=None):
        self.backfilled.append(table)
        return {"queued": 7, "skipped": 0}


def _proposal(pid, status="auto_approved", unmet=(), needs=0, rejected=0):
    return {"proposal_id": pid, "status": status, "unmet_required": list(unmet),
            "needs_review": needs, "rejected": rejected, "gemini_error": None}


@pytest.fixture()
def wire(monkeypatch):
    def _wire(rec: _Recorder):
        class _FakePipeline:
            _query = staticmethod(rec.query)

        monkeypatch.setattr(onboarding, "_pipeline", lambda: _FakePipeline)
        monkeypatch.setattr(onboarding, "discover", rec.discover)
        monkeypatch.setattr(onboarding, "propose", rec.propose)
        monkeypatch.setattr(onboarding, "deploy", rec.deploy)
        monkeypatch.setattr(onboarding, "backfill", rec.backfill)
        monkeypatch.setattr(onboarding, "MySQLStagingConnector", lambda: object())
        return rec
    return _wire


def _run(tables, central=None):
    return onboarding.onboard_bulk("irimsv", tables, "lrmis", "tester",
                                   central=central or _FakeCentral())


# ---------------------------------------------------------------------------
# Conservative bucketing
# ---------------------------------------------------------------------------

def test_confident_table_is_deployed_and_backfilled(wire):
    rec = wire(_Recorder({"farmers": _proposal(11)}))
    result = _run(["farmers"])
    assert result["counts"]["onboarded"] == 1
    assert rec.deployed == [11] and rec.backfilled == ["farmers"]


def test_mid_confidence_table_is_never_deployed(wire):
    rec = wire(_Recorder({"schools": _proposal(12, status="needs_review", needs=2)}))
    result = _run(["schools"])
    assert result["counts"]["needs_review"] == 1
    assert result["needs_review"][0]["proposal_id"] == 12
    assert rec.deployed == [] and rec.backfilled == []


def test_unmet_required_column_blocks_deploy_even_when_auto_approved(wire):
    rec = wire(_Recorder({"crops": _proposal(13, unmet=["staging.region_id"])}))
    result = _run(["crops"])
    assert result["counts"]["needs_review"] == 1
    assert result["needs_review"][0]["unmet_required"] == ["staging.region_id"]
    assert rec.deployed == []


def test_already_deployed_table_is_skipped_untouched(wire):
    rec = wire(_Recorder({"farmers": _proposal(14)},
                         entity_status={"farmers": "deployed"}))
    result = _run(["farmers"])
    assert result["counts"]["skipped_already_deployed"] == 1
    # Non-destructive: no deploy (no drop/recreate) and no propose call.
    assert rec.deployed == [] and rec.backfilled == [] and rec.discovered == 0


# ---------------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------------

def test_failure_does_not_abort_the_batch(wire):
    rec = wire(_Recorder(
        {"a": _proposal(1), "b": _proposal(2), "c": _proposal(3)},
        raise_on={"b": "gemini exploded"},
    ))
    result = _run(["a", "b", "c"])
    assert result["counts"] == {"onboarded": 2, "needs_review": 0,
                                "skipped_already_deployed": 0, "failed": 1}
    assert rec.deployed == [1, 3]           # c still ran after b failed
    assert result["failed"][0]["table"] == "b"
    assert "gemini exploded" in result["failed"][0]["error"]


def test_mixed_batch_reports_every_bucket(wire):
    rec = wire(_Recorder(
        {
            "ok": _proposal(1),
            "review": _proposal(2, status="needs_review", needs=1),
            "boom": _proposal(3),
            "done": _proposal(4),
        },
        entity_status={"done": "deployed"},
        raise_on={"boom": "no such table"},
    ))
    result = _run(["ok", "review", "boom", "done"])
    assert result["counts"] == {"onboarded": 1, "needs_review": 1,
                                "skipped_already_deployed": 1, "failed": 1}
    assert result["requested"] == 4


def test_progress_is_reported_per_table(wire):
    wire(_Recorder({"a": _proposal(1), "b": _proposal(2)}))
    seen = []
    onboarding.onboard_bulk("irimsv", ["a", "b"], "LRMIS", "tester",
                            central=_FakeCentral(),
                            progress=lambda i, n, msg: seen.append((i, n)))
    assert seen == [(0, 2), (1, 2), (2, 2)]  # per table, then completion


def test_target_system_is_normalized(wire):
    wire(_Recorder({"a": _proposal(1)}))
    result = _run(["a"])
    assert result["target_system"] == "LRMIS"
