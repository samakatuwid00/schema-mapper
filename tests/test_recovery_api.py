"""Recovery API integration tests (source-schema-swap-and-disaster-recovery §4.6).

Full upload → validate → list → restore flow through the real FastAPI routes
and the REAL `backup_recovery` service code — only the central DB (an
in-memory fake connector), the shell runner (a spy), and the audit writer (a
capture list) are substituted. No live databases, nothing destructive runs.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("ADMIN_SESSION_SECRET", "test-secret-for-unit-tests")

from fastapi.testclient import TestClient

import src.admin_api.audit as audit_module
from src.admin_api.app import create_app
from src.admin_api.auth import AdminUser, current_user
from src.services import backup_recovery

VALID_SOURCE_SQL = ("-- PostgreSQL database dump\nCREATE SCHEMA irimsv;\n"
                    "CREATE TABLE irimsv.schools (id integer);\n")
VALID_TARGET_SQL = "-- MySQL dump 10.13\nCREATE TABLE `school` (`id` int);\n"


class _FakeCentral:
    """Stateful in-memory stand-in for the central DB's recovery_upload table."""

    def __init__(self):
        self.rows: list[dict] = []
        self.next_id = 1

    def connection(self):
        central = self

        class _Cur:
            def __enter__(cur):
                return cur

            def __exit__(cur, *a):
                return False

            def execute(cur, sql, params=None):
                cur._result, cur._all = None, []
                if "INSERT INTO integration.recovery_upload" in sql:
                    kind, fn, path, checksum, size, valid, reason, by = params
                    row = {"id": central.next_id, "kind": kind,
                           "original_filename": fn, "stored_path": path,
                           "checksum": checksum, "size_bytes": size,
                           "valid": valid, "invalid_reason": reason,
                           "uploaded_by": by,
                           "uploaded_at": datetime.now(timezone.utc),
                           "used_at": None, "used_by": None}
                    central.next_id += 1
                    central.rows.append(row)
                    cur._result = dict(row)
                elif "UPDATE integration.recovery_upload" in sql:
                    by, upload_id = params
                    for row in central.rows:
                        if row["id"] == upload_id:
                            row["used_at"] = datetime.now(timezone.utc)
                            row["used_by"] = by
                elif "WHERE id = %s" in sql:
                    match = [r for r in central.rows if r["id"] == params[0]]
                    cur._result = dict(match[0]) if match else None
                elif "FROM integration.recovery_upload" in sql:
                    cur._all = [dict(r) for r in
                                sorted(central.rows,
                                       key=lambda r: r["uploaded_at"], reverse=True)]

            def fetchone(cur):
                return cur._result

            def fetchall(cur):
                return cur._all

        class _Conn:
            def __enter__(conn):
                return conn

            def __exit__(conn, *a):
                return False

            def cursor(conn, cursor_factory=None):
                return _Cur()

            def commit(conn):
                pass

        return _Conn()

    def close(self):
        pass


@pytest.fixture()
def harness(tmp_path, monkeypatch):
    """App + fakes: fake central, tmp backup/upload dirs, spy runner, audit sink."""
    fake = _FakeCentral()
    shell_calls: list[str] = []
    audits: list[dict] = []

    monkeypatch.setattr(backup_recovery, "PostgresCentralConnector", lambda: fake)
    monkeypatch.setattr(backup_recovery, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(backup_recovery, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(
        backup_recovery, "_run_shell",
        lambda cmd: shell_calls.append(cmd) or
        {"command": cmd, "returncode": 0, "executed": True})
    monkeypatch.setattr(
        audit_module, "write_audit",
        lambda actor, action, **kw: audits.append({"actor": actor,
                                                   "action": action, **kw}))
    monkeypatch.setenv("LRMIS_SOURCE_RESTORE_CMD", "restore-tool {dump}")

    os.makedirs(tmp_path / "backups", exist_ok=True)
    with open(tmp_path / "backups" / "lrmis_target-20260714-010203.sql", "w",
              encoding="utf-8") as handle:
        handle.write(VALID_TARGET_SQL)

    app = create_app()
    app.dependency_overrides[current_user] = lambda: AdminUser(1, "tester", "operator")
    client = TestClient(app)
    yield {"client": client, "central": fake, "shell": shell_calls,
           "audits": audits, "tmp": tmp_path}
    app.dependency_overrides.clear()


def _upload(client, content: bytes, kind: str, name="dump.sql"):
    return client.post("/api/recovery/upload",
                       files={"file": (name, content, "application/octet-stream")},
                       data={"kind": kind})


def test_full_source_flow_upload_validate_list_restore_audit(harness):
    client = harness["client"]

    # upload → validated + quarantined + recorded
    response = _upload(client, VALID_SOURCE_SQL.encode("utf-8"), "source_dump")
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["valid"] is True and row["validation"]["ok"] is True
    assert str(harness["tmp"] / "uploads") in row["stored_path"]

    # list → the upload and the pre-existing nightly backup both appear
    listed = client.get("/api/recovery/backups").json()
    assert [u["id"] for u in listed["uploads"]] == [row["id"]]
    assert [b["id"] for b in listed["target_backups"]] == [
        "lrmis_target-20260714-010203.sql"]

    # restore without the typed confirmation → 422, nothing executed
    response = client.post("/api/recovery/restore-source", json={
        "upload_id": row["id"], "reason": "recover from bad nightly dump"})
    assert response.status_code == 422
    assert "typed confirmation" in response.json()["detail"]
    assert harness["shell"] == []

    # confirmed restore → executes the configured command, marks used, audits
    response = client.post("/api/recovery/restore-source", json={
        "upload_id": row["id"], "confirm": "irimsv",
        "reason": "recover from bad nightly dump"})
    assert response.status_code == 200, response.text
    assert response.json()["executed"] is True
    assert harness["shell"] == [f"restore-tool {row['stored_path']}"]
    stored = harness["central"].rows[0]
    assert stored["used_by"] == "tester" and stored["used_at"] is not None
    actions = [a["action"] for a in harness["audits"]]
    assert "recovery_upload" in actions and "recovery_restore_source" in actions
    restore_audit = [a for a in harness["audits"]
                     if a["action"] == "recovery_restore_source"][-1]
    assert restore_audit["result"] == "success"
    assert restore_audit["reason"] == "recover from bad nightly dump"


def test_utf16_upload_is_recorded_invalid_with_specific_reason(harness):
    client = harness["client"]
    response = _upload(client, VALID_SOURCE_SQL.encode("utf-16"), "source_dump")
    assert response.status_code == 200
    row = response.json()
    assert row["valid"] is False
    assert "UTF-16" in row["invalid_reason"]

    # ...and a restore from it is refused even WITH confirmation
    response = client.post("/api/recovery/restore-source", json={
        "upload_id": row["id"], "confirm": "irimsv", "reason": "try anyway"})
    assert response.status_code == 422
    assert "failed validation" in response.json()["detail"]
    assert harness["shell"] == []


def test_full_target_flow_from_listed_backup(harness):
    client = harness["client"]

    # reason is mandatory
    response = client.post("/api/recovery/restore-target", json={
        "backup_id": "lrmis_target-20260714-010203.sql",
        "confirm": "lrmis_target"})
    assert response.status_code == 422

    # typed confirmation is mandatory
    response = client.post("/api/recovery/restore-target", json={
        "backup_id": "lrmis_target-20260714-010203.sql",
        "reason": "rebuild failed partway"})
    assert response.status_code == 422
    assert harness["shell"] == []

    # dry-run previews without executing
    response = client.post("/api/recovery/restore-target", json={
        "backup_id": "lrmis_target-20260714-010203.sql",
        "confirm": "lrmis_target", "reason": "rebuild failed partway",
        "dry_run": True})
    assert response.status_code == 200
    assert response.json()["executed"] is False
    assert harness["shell"] == []

    # confirmed restore executes the mysql replay of the backup
    response = client.post("/api/recovery/restore-target", json={
        "backup_id": "lrmis_target-20260714-010203.sql",
        "confirm": "lrmis_target", "reason": "rebuild failed partway"})
    assert response.status_code == 200, response.text
    assert response.json()["executed"] is True
    assert len(harness["shell"]) == 1
    assert "lrmis_target-20260714-010203.sql" in harness["shell"][0]
    audit = [a for a in harness["audits"]
             if a["action"] == "recovery_restore_target"][-1]
    assert audit["result"] == "success"


def test_target_flow_from_validated_upload_marks_used(harness):
    client = harness["client"]
    row = _upload(client, VALID_TARGET_SQL.encode("utf-8"), "target_backup",
                  name="manual-backup.sql").json()
    assert row["valid"] is True

    response = client.post("/api/recovery/restore-target", json={
        "backup_id": str(row["id"]), "confirm": "lrmis_target",
        "reason": "restore manual backup"})
    assert response.status_code == 200, response.text
    stored = harness["central"].rows[0]
    assert stored["used_by"] == "tester" and stored["used_at"] is not None


def test_recovery_routes_require_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_recovery, "BACKUP_DIR", str(tmp_path))
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/recovery/backups").status_code == 401
        assert client.post("/api/recovery/restore-target", json={}).status_code == 401
        assert client.post("/api/recovery/restore-source", json={}).status_code == 401
        response = client.post("/api/recovery/upload",
                               files={"file": ("a.sql", b"x")},
                               data={"kind": "source_dump"})
        assert response.status_code == 401


def test_upload_rejects_unknown_kind(harness):
    response = _upload(harness["client"], b"CREATE SCHEMA irimsv;", "everything")
    assert response.status_code == 422
