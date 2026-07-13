"""drift_resolution: source/target resolution, dry-run, coordinator, audit.

The DB and the reused primitives (``ops.refresh``, ``_entity_fingerprints``) are
stubbed, so what is exercised is the resolution contract: which paused entities
are picked up, that the staging refresh runs before the fingerprint is stored,
that the pause is cleared on both tables, that an audit row is written, that
drift reports flip to resolved once their entities are cleared, and that dry-run
mutates nothing.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from src.services import drift_resolution as dr


class _FakeConn:
    def commit(self):
        pass

    def rollback(self):
        pass


class _FakeCentral:
    @contextmanager
    def connection(self):
        yield _FakeConn()

    def close(self):
        pass


class _FakePipeline:
    """In-memory stand-in for the pipeline SQL helpers used by the module."""

    def __init__(self, entities, reports=None):
        self.entities = entities
        self.reports = reports or []
        self.executed: list[tuple[str, tuple]] = []
        self.audits: list[tuple] = []

    # -- reads --------------------------------------------------------------
    def _query(self, conn, sql, params=None):
        # The module's only _query is _drifted_entities.
        marker = params[0].strip("%")            # "source=True" | "target=True"
        rows = [e for e in self.entities
                if e["status"] == "paused"
                and str(e.get("paused_reason", "")).startswith("Schema drift detected:")
                and marker in e["paused_reason"]]
        if params and len(params) > 1:
            allowed = set(params[1])
            rows = [e for e in rows if e["source_table"] in allowed]
        return rows

    # -- writes -------------------------------------------------------------
    def _execute(self, conn, sql, params=None):
        self.executed.append((sql, params))
        s = " ".join(sql.split())
        if "UPDATE integration.onboarding_entity" in s and "status = 'deployed'" in s:
            new_fp, entity_id = params[0], params[-1]
            entity = self._by_id(entity_id)
            if entity:
                if "source_fingerprint" in s:
                    entity["source_fingerprint"] = new_fp
                if "target_fingerprint" in s:
                    entity["target_fingerprint"] = new_fp
                entity["status"] = "deployed"
                entity["paused_reason"] = None
            return 1
        if "UPDATE integration.entity_control" in s:
            return 1
        if "INSERT INTO integration.onboarding_audit" in s:
            self.audits.append(params)
            return 1
        if "UPDATE integration.schema_drift_report" in s:
            actor, marked = params[0], 0
            for report in self.reports:
                impacted = report.get("impacted_entities") or []
                if report.get("resolved_at") is None and impacted and \
                        all(not self._is_paused(name) for name in impacted):
                    report["resolved_at"], report["resolved_by"] = "now", actor
                    marked += 1
            return marked
        return 1

    def _by_id(self, entity_id):
        return next((e for e in self.entities if e["id"] == entity_id), None)

    def _is_paused(self, source_table):
        return any(e["source_table"] == source_table and e["status"] == "paused"
                   for e in self.entities)


def _entity(id, table, *, source=False, target=False, schema="irimsv"):
    return {
        "id": id, "source_schema": schema, "source_table": table,
        "target_system": "LRMIS", "staging_table": f"irimsv_{table}_staging",
        "source_fingerprint": "src_old", "target_fingerprint": "tgt_old",
        "status": "paused",
        "paused_reason": f"Schema drift detected: source={source}, target={target}",
    }


class _RefreshRecorder:
    def __init__(self, status="refreshed"):
        self.status = status
        self.calls: list[tuple] = []

    def __call__(self, source_schema, tables, target_system, **kwargs):
        self.calls.append((source_schema, tuple(tables), target_system))
        table = tables[0]
        return {"results": [{"table": table, "status": self.status,
                             "target_table": f"irimsv_{table}_staging",
                             "rows_loaded": 42, "snapshot": "snap-1"}]}


@pytest.fixture()
def wire(monkeypatch):
    def _wire(pipeline: _FakePipeline, refresh: _RefreshRecorder | None = None,
              fingerprints=("src_new", "tgt_new")):
        refresh = refresh or _RefreshRecorder()
        monkeypatch.setattr(dr, "_pipeline", lambda: pipeline)
        monkeypatch.setattr(dr.ops_service, "refresh", refresh)
        monkeypatch.setattr(dr, "_entity_fingerprints",
                            lambda conn, staging, entity: fingerprints)
        return refresh
    return _wire


def _run(fn, pipeline, **kwargs):
    return fn(central=_FakeCentral(), staging=object(), **kwargs)


# ---------------------------------------------------------------------------
# 6.1 source resolution
# ---------------------------------------------------------------------------

def test_resolve_source_drift_refreshes_and_reenables(wire):
    pipeline = _FakePipeline([_entity(1, "farmers", source=True)])
    refresh = wire(pipeline)
    result = _run(dr.resolve_source_drift, pipeline, actor="alice")

    assert result["resolved_count"] == 1 and result["skipped_count"] == 0
    assert result["resolved"][0] == {"entity": "farmers", "kind": "source",
                                     "old_fingerprint": "src_old",
                                     "new_fingerprint": "src_new", "rows_loaded": 42}
    # staging was refreshed for exactly this entity
    assert refresh.calls == [("irimsv", ("farmers",), "LRMIS")]
    # entity fingerprint updated, pause cleared, delivery re-enabled
    assert pipeline.entities[0]["source_fingerprint"] == "src_new"
    assert pipeline.entities[0]["status"] == "deployed"
    assert pipeline.entities[0]["paused_reason"] is None
    assert any("UPDATE integration.entity_control" in " ".join(sql.split())
               for sql, _ in pipeline.executed)
    # audited
    assert len(pipeline.audits) == 1


def test_resolve_source_drift_reports_progress(wire):
    pipeline = _FakePipeline([_entity(1, "farmers", source=True)])
    wire(pipeline)
    seen = []
    dr.resolve_source_drift(central=_FakeCentral(), staging=object(),
                            progress=lambda i, n, msg: seen.append((i, n)))
    assert (0, 1) in seen and (1, 1) in seen        # per entity, then completion


def test_resolve_source_drift_skips_when_refresh_did_not_run(wire):
    pipeline = _FakePipeline([_entity(1, "farmers", source=True)])
    wire(pipeline, refresh=_RefreshRecorder(status="skipped"))
    result = _run(dr.resolve_source_drift, pipeline)
    assert result["resolved_count"] == 0 and result["skipped_count"] == 1
    # no fingerprint written, entity still paused
    assert pipeline.entities[0]["status"] == "paused"
    assert pipeline.audits == []


# ---------------------------------------------------------------------------
# 6.2 target resolution
# ---------------------------------------------------------------------------

def test_resolve_target_drift_updates_target_fingerprint(wire):
    pipeline = _FakePipeline([_entity(1, "schools", target=True)])
    wire(pipeline)
    result = _run(dr.resolve_target_drift, pipeline)
    assert result["resolved_count"] == 1
    assert result["resolved"][0]["new_fingerprint"] == "tgt_new"
    assert pipeline.entities[0]["target_fingerprint"] == "tgt_new"
    assert pipeline.entities[0]["source_fingerprint"] == "src_old"   # untouched
    assert pipeline.entities[0]["status"] == "deployed"


def test_target_resolution_ignores_source_only_drift(wire):
    pipeline = _FakePipeline([_entity(1, "farmers", source=True)])
    wire(pipeline)
    result = _run(dr.resolve_target_drift, pipeline)
    assert result["resolved_count"] == 0        # source-only entity not a target target


# ---------------------------------------------------------------------------
# 6.3 dry-run
# ---------------------------------------------------------------------------

def test_dry_run_mutates_nothing(wire):
    pipeline = _FakePipeline([_entity(1, "farmers", source=True)])
    refresh = wire(pipeline)
    result = _run(dr.resolve_source_drift, pipeline, dry_run=True)

    assert result["dry_run"] is True
    assert result["resolved"] == [] and result["resolved_count"] == 0
    assert result["plan"][0] == {"entity": "farmers", "kind": "source",
                                 "current_fingerprint": "src_old",
                                 "new_fingerprint": "src_new", "changed": True,
                                 "action": "refresh staging, update fingerprint, re-enable"}
    # nothing was refreshed or written
    assert refresh.calls == []
    assert pipeline.executed == []
    assert pipeline.entities[0]["status"] == "paused"


# ---------------------------------------------------------------------------
# 6.4 resolve_all coordinator
# ---------------------------------------------------------------------------

def test_resolve_all_runs_source_then_target(wire):
    pipeline = _FakePipeline([
        _entity(1, "farmers", source=True),
        _entity(2, "schools", target=True),
    ])
    wire(pipeline)
    result = _run(dr.resolve_all, pipeline, actor="bob")
    assert result["resolved_count"] == 2 and result["skipped_count"] == 0
    assert result["source"]["resolved_count"] == 1
    assert result["target"]["resolved_count"] == 1
    assert pipeline.entities[0]["status"] == "deployed"
    assert pipeline.entities[1]["status"] == "deployed"


def test_resolve_drift_direction_flags(wire):
    pipeline = _FakePipeline([
        _entity(1, "farmers", source=True),
        _entity(2, "schools", target=True),
    ])
    wire(pipeline)
    # target only
    result = dr.resolve_drift(resolve_source=False, resolve_target=True,
                              central=_FakeCentral(), staging=object())
    assert result["resolved_count"] == 1
    assert pipeline.entities[1]["status"] == "deployed"
    assert pipeline.entities[0]["status"] == "paused"          # source not touched


# ---------------------------------------------------------------------------
# 6.5 integration-style: report flips to resolved once entities clear
# ---------------------------------------------------------------------------

def test_drift_report_marked_resolved_when_entities_cleared(wire):
    report = {"id": 7, "impacted_entities": ["farmers"], "resolved_at": None}
    pipeline = _FakePipeline([_entity(1, "farmers", source=True)], reports=[report])
    wire(pipeline)
    result = _run(dr.resolve_source_drift, pipeline, actor="carol")

    assert result["reports_marked_resolved"] == 1
    assert report["resolved_at"] == "now" and report["resolved_by"] == "carol"


def test_report_stays_open_while_an_entity_is_still_paused(wire):
    report = {"id": 7, "impacted_entities": ["farmers", "schools"], "resolved_at": None}
    pipeline = _FakePipeline([
        _entity(1, "farmers", source=True),
        _entity(2, "schools", source=False, target=False),   # not drift-paused → left paused
    ], reports=[report])
    # schools is paused for a non-drift reason, so it is never resolved here
    pipeline.entities[1]["paused_reason"] = "manually paused"
    wire(pipeline)
    _run(dr.resolve_source_drift, pipeline)
    assert report["resolved_at"] is None        # schools still paused → report stays open


# ---------------------------------------------------------------------------
# 6.6 job handler param handling
# ---------------------------------------------------------------------------

def test_job_handler_parses_params(monkeypatch):
    from src.admin_api import jobs

    captured = {}

    def _fake_resolve_drift(**kwargs):
        captured.update(kwargs)
        return {"resolved_count": 0}

    monkeypatch.setattr(jobs.drift_service, "resolve_drift", _fake_resolve_drift)

    class _Ctx:
        def progress(self, *a, **k):
            pass

    jobs._h_resolve_drift(
        {"_actor": "dave", "resolve_source": "false", "resolve_target": True,
         "entities": "farmers, schools", "dry_run": "true"}, _Ctx())

    assert captured["resolve_source"] is False          # "false" string coerced
    assert captured["resolve_target"] is True
    assert captured["dry_run"] is True
    assert captured["entities"] == ["farmers", "schools"]
    assert captured["actor"] == "dave"


def test_resolve_drift_is_allowlisted_and_scoped():
    from src.admin_api import jobs
    assert "resolve_drift" in jobs.JOB_HANDLERS          # accepted
    assert "shell_exec" not in jobs.JOB_HANDLERS         # rejected
    assert jobs._SCOPED["resolve_drift"]({}) == "resolve-drift"
