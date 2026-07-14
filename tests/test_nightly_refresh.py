"""Nightly rebuild orchestration (nightly-refresh spec).

The connectors, source restore, target reset, and per-entity refresh are faked so
no database, dump file, or LRMIS DDL is needed; what is exercised is the
orchestration: ordering, restore-aborts-before-reset, dry-run-changes-nothing,
and continue-past-a-failing-entity.
"""
from __future__ import annotations

import os

import pytest

from src.services import nightly_refresh as NR
from src.services.common import ValidationError


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Conn:
    def cursor(self, cursor_factory=None):
        class _C:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def execute(self_, *a): pass
            def fetchone(self_): return (0,)
            def fetchall(self_): return []
        return _C()

    def commit(self): pass
    def rollback(self): pass


class _CM:
    def __enter__(self): return _Conn()
    def __exit__(self, *a): return False


class _Central:
    def connection(self): return _CM()
    def close(self): pass


class _Target:
    @classmethod
    def for_target(cls): return cls()
    def connection(self): return _CM()


@pytest.fixture
def fake_connectors(monkeypatch):
    monkeypatch.setattr(NR, "PostgresCentralConnector", lambda: _Central())
    monkeypatch.setattr(NR, "MySQLStagingConnector", _Target)
    # Enumeration and counts are exercised elsewhere; keep the orchestration hermetic.
    monkeypatch.setattr(NR, "deployed_target_entities",
                        lambda conn, target_system=NR.TARGET_SYSTEM: [
                            {"source_schema": "irimsv", "source_table": "schools",
                             "primary_key_columns": ["id"], "source_system": "IRIMSV_REGION_V"}])
    monkeypatch.setattr(NR, "_source_count", lambda conn, s, t: 42)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def test_dry_run_changes_nothing(fake_connectors, monkeypatch):
    calls = []
    monkeypatch.setattr(NR, "recreate_target_database",
                        lambda dry_run=False: calls.append(("reset", dry_run)) or {"dry_run": dry_run})
    monkeypatch.setattr(NR, "redeliver_all",
                        lambda *a, **k: calls.append(("redeliver", a)) or pytest.fail("must not redeliver in dry run"))

    out = NR.run_nightly_refresh(actor="tester", restore=False, dry_run=True)

    assert out["steps"]["reset"] == {"dry_run": True}
    assert out["steps"]["redeliver"] == {"skipped": "dry_run"}
    assert out["steps"]["source_counts"] == {"schools": 42}
    assert ("reset", True) in calls


def test_ordering_restore_backup_reset_redeliver(fake_connectors, monkeypatch):
    order = []
    monkeypatch.setattr(NR, "restore_source_dump",
                        lambda **k: order.append("restore") or {"executed": True})
    monkeypatch.setattr(NR, "backup_target",
                        lambda **k: order.append("backup") or {"executed": True})
    monkeypatch.setattr(NR, "recreate_target_database",
                        lambda dry_run=False: order.append("reset") or {"created": 51})
    monkeypatch.setattr(NR, "redeliver_all",
                        lambda entities, target_system=NR.TARGET_SYSTEM, progress=None:
                        order.append("redeliver") or [{"status": "refreshed"}])

    NR.run_nightly_refresh(actor="tester", restore=True, dry_run=False)

    assert order == ["restore", "backup", "reset", "redeliver"]


def test_restore_failure_aborts_before_reset(fake_connectors, monkeypatch):
    reset_called = []
    monkeypatch.setattr(NR, "restore_source_dump",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("pg_restore blew up")))
    monkeypatch.setattr(NR, "recreate_target_database",
                        lambda dry_run=False: reset_called.append(True))
    monkeypatch.setattr(NR, "redeliver_all", lambda *a, **k: pytest.fail("must not redeliver"))

    with pytest.raises(RuntimeError, match="pg_restore"):
        NR.run_nightly_refresh(actor="tester", restore=True, dry_run=False)
    assert reset_called == []          # target never touched when restore fails


def test_restore_skipped_when_not_requested(fake_connectors, monkeypatch):
    monkeypatch.setattr(NR, "restore_source_dump",
                        lambda **k: pytest.fail("restore must not run when restore=False"))
    monkeypatch.setattr(NR, "backup_target", lambda **k: {"executed": True})
    monkeypatch.setattr(NR, "recreate_target_database", lambda dry_run=False: {"created": 51})
    monkeypatch.setattr(NR, "redeliver_all", lambda *a, **k: [{"status": "refreshed"}])

    out = NR.run_nightly_refresh(actor="tester", restore=False, dry_run=False)
    assert "restore" not in out["steps"]


# ---------------------------------------------------------------------------
# Guarded source restore
# ---------------------------------------------------------------------------

def test_restore_requires_a_dump_path(monkeypatch):
    monkeypatch.delenv("LRMIS_SOURCE_DUMP_PATH", raising=False)
    with pytest.raises(ValidationError, match="no source dump path"):
        NR.restore_source_dump()


def test_restore_refuses_to_guess_command(monkeypatch):
    monkeypatch.delenv("LRMIS_SOURCE_RESTORE_CMD", raising=False)
    monkeypatch.delenv("CENTRAL_DSN", raising=False)
    with pytest.raises(ValidationError, match="not configured"):
        NR.restore_source_dump(dump_path="dump.sql")


def test_restore_dry_run_reports_command_without_executing(monkeypatch):
    plan = NR.restore_source_dump(dump_path="dump.sql",
                                  restore_cmd="mycmd {dump}", dry_run=True)
    assert plan["executed"] is False
    assert plan["command"] == "mycmd dump.sql"


# ---------------------------------------------------------------------------
# redeliver_all: continue past a failing entity
# ---------------------------------------------------------------------------

def test_redeliver_all_continues_past_failure(monkeypatch):
    monkeypatch.setattr(NR, "PostgresCentralConnector", lambda: _Central())
    monkeypatch.setattr(NR, "MySQLStagingConnector", _Target)

    def fake_one(cconn, tconn, entity, target_system, writer=None, registry=None):
        if entity["source_table"] == "boom":
            raise RuntimeError("delivery exploded")
        return {"entity": entity["source_table"], "status": "refreshed"}

    monkeypatch.setattr(NR, "_redeliver_entity", fake_one)

    entities = [{"source_table": "a"}, {"source_table": "boom"}, {"source_table": "c"}]
    results = NR.redeliver_all(entities)

    assert [r["status"] for r in results] == ["refreshed", "failed", "refreshed"]
    assert results[1]["error"] == "delivery exploded"


def test_redeliver_entity_skips_without_mappings(monkeypatch):
    monkeypatch.setattr(NR, "load_entity_mappings", lambda cconn, table, ts: [])
    out = NR._redeliver_entity(_Conn(), _Conn(),
                               {"source_schema": "irimsv", "source_table": "schools",
                                "primary_key_columns": ["id"]}, "LRMIS")
    assert out["status"] == "skipped"


# ---------------------------------------------------------------------------
# backup_target: discard a partial/empty file on failure
# ---------------------------------------------------------------------------

class _FakeCompleted:
    def __init__(self, returncode, stderr=""):
        self.returncode = returncode
        self.stderr = stderr


class _FakeSubprocess:
    """Stand-in for the `subprocess` module backup_target calls into.

    backup_target() opens `out_path` for writing before it ever calls
    subprocess.run(), so faking only `.run()` (success / nonzero returncode /
    raises) reproduces the real 0-byte-file bug scenario exactly: the file
    exists on disk from the `open()` call, and nothing was ever written to it.
    """
    PIPE = object()

    def __init__(self, returncode=0, stderr="", raises=None):
        self._returncode = returncode
        self._stderr = stderr
        self._raises = raises

    def run(self, *args, **kwargs):
        if self._raises is not None:
            raise self._raises
        return _FakeCompleted(self._returncode, self._stderr)


def test_backup_target_dry_run_reports_plan_without_touching_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(NR, "BACKUP_DIR", str(tmp_path))

    plan = NR.backup_target(dry_run=True)

    assert plan["executed"] is False
    assert list(tmp_path.iterdir()) == []


def test_backup_target_success_keeps_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(NR, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(NR, "subprocess", _FakeSubprocess(returncode=0))

    plan = NR.backup_target(dry_run=False)

    assert plan["executed"] is True
    assert "warning" not in plan
    assert os.path.exists(plan["path"])


def test_backup_target_deletes_empty_file_on_nonzero_returncode(tmp_path, monkeypatch):
    monkeypatch.setattr(NR, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(NR, "subprocess",
                        _FakeSubprocess(returncode=1, stderr="mysqldump: connection refused"))

    plan = NR.backup_target(dry_run=False)

    assert plan["executed"] is False
    assert "connection refused" in plan["warning"]
    assert not os.path.exists(plan["path"]), \
        "a failed dump must not leave a 0-byte file behind"


def test_backup_target_deletes_partial_file_on_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(NR, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(NR, "subprocess",
                        _FakeSubprocess(raises=FileNotFoundError("mysqldump: not found")))

    plan = NR.backup_target(dry_run=False)

    assert plan["executed"] is False
    assert "not found" in plan["warning"]
    assert not os.path.exists(plan["path"]), \
        "a raised exception must not leave a partial file behind either"


# ---------------------------------------------------------------------------
# run_nightly_refresh: surface step-level warnings at the top level
# ---------------------------------------------------------------------------

def test_run_nightly_refresh_surfaces_backup_warning(fake_connectors, monkeypatch):
    monkeypatch.setattr(NR, "backup_target",
                        lambda **k: {"executed": False, "warning": "mysqldump not found"})
    monkeypatch.setattr(NR, "recreate_target_database", lambda dry_run=False: {"created": 51})
    monkeypatch.setattr(NR, "redeliver_all", lambda *a, **k: [{"status": "refreshed"}])

    out = NR.run_nightly_refresh(actor="tester", restore=False, dry_run=False)

    assert out["warnings"] == [{"step": "backup", "warning": "mysqldump not found"}]


def test_run_nightly_refresh_omits_warnings_key_when_clean(fake_connectors, monkeypatch):
    monkeypatch.setattr(NR, "backup_target", lambda **k: {"executed": True})
    monkeypatch.setattr(NR, "recreate_target_database", lambda dry_run=False: {"created": 51})
    monkeypatch.setattr(NR, "redeliver_all", lambda *a, **k: [{"status": "refreshed"}])

    out = NR.run_nightly_refresh(actor="tester", restore=False, dry_run=False)

    assert "warnings" not in out
