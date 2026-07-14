"""Disaster-recovery core tests (source-schema-swap-and-disaster-recovery §3).

File validation is tested against real temp files — including the exact
historical failure (a source dump written UTF-16 by a PowerShell `>` redirect,
recorded in docs/RUNBOOK_source_to_target.md) reproduced as a fixture. The
staging/restore paths run against a fake central connector and a spy runner:
no live databases, and nothing destructive executes.
"""
import io
import os

import pytest

from src.services.backup_recovery import (
    _safe_filename, list_target_backups, restore_source, restore_target,
    stage_upload, validate_upload,
)
from src.services.common import NotFoundError, ValidationError

VALID_SOURCE_SQL = (
    "-- PostgreSQL database dump\n"
    "CREATE SCHEMA irimsv;\n"
    "CREATE TABLE irimsv.schools (id integer PRIMARY KEY, name text);\n"
    "INSERT INTO irimsv.schools VALUES (1, 'x');\n")

VALID_TARGET_SQL = (
    "-- MySQL dump 10.13  Distrib 8.0\n"
    "CREATE TABLE `school` (`id` int NOT NULL);\n"
    "INSERT INTO `school` VALUES (1);\n")


def _write(tmp_path, name, data):
    path = tmp_path / name
    mode = "wb" if isinstance(data, bytes) else "w"
    kwargs = {} if isinstance(data, bytes) else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as handle:
        handle.write(data)
    return str(path)


# --- validate_upload ---------------------------------------------------------

def test_valid_utf8_source_dump_accepted(tmp_path):
    path = _write(tmp_path, "dump.sql", VALID_SOURCE_SQL)
    verdict = validate_upload(path, "source_dump")
    assert verdict == {"ok": True, "reason": None, "format": "sql"}


def test_utf16_dump_rejected_with_specific_reason(tmp_path):
    """The recorded incident: PowerShell `>` redirection wrote the dump
    UTF-16LE with BOM and psql could not read it."""
    path = _write(tmp_path, "dump.sql", VALID_SOURCE_SQL.encode("utf-16"))
    verdict = validate_upload(path, "source_dump")
    assert verdict["ok"] is False
    assert "UTF-16" in verdict["reason"]
    assert "UTF-8" in verdict["reason"]


def test_bomless_utf16_dump_also_rejected(tmp_path):
    path = _write(tmp_path, "dump.sql", VALID_SOURCE_SQL.encode("utf-16-le"))
    verdict = validate_upload(path, "source_dump")
    assert verdict["ok"] is False
    assert "UTF-16" in verdict["reason"]


def test_non_dump_file_rejected_by_magic_check(tmp_path):
    path = _write(tmp_path, "img.sql", b"\x89PNG\r\n\x1a\n" + os.urandom(64))
    verdict = validate_upload(path, "source_dump")
    assert verdict["ok"] is False
    assert "not a database dump" in verdict["reason"]


def test_source_dump_missing_irimsv_schema_rejected(tmp_path):
    path = _write(tmp_path, "other.sql",
                  "CREATE SCHEMA other;\nCREATE TABLE other.t (id int);\n")
    verdict = validate_upload(path, "source_dump")
    assert verdict["ok"] is False
    assert "irimsv" in verdict["reason"]


def test_pg_custom_archive_source_dump_accepted(tmp_path):
    path = _write(tmp_path, "dump.backup",
                  b"PGDMP\x01\x0e\x00...toc...irimsv...schools..." + os.urandom(32))
    verdict = validate_upload(path, "source_dump")
    assert verdict == {"ok": True, "reason": None, "format": "pg_custom"}


def test_pg_custom_archive_rejected_as_target_backup(tmp_path):
    path = _write(tmp_path, "dump.backup", b"PGDMP\x01irimsv")
    verdict = validate_upload(path, "target_backup")
    assert verdict["ok"] is False
    assert "plain-SQL" in verdict["reason"]


def test_mysqldump_target_backup_accepted(tmp_path):
    path = _write(tmp_path, "backup.sql", VALID_TARGET_SQL)
    assert validate_upload(path, "target_backup")["ok"] is True


def test_empty_file_rejected(tmp_path):
    path = _write(tmp_path, "empty.sql", b"")
    assert validate_upload(path, "source_dump")["ok"] is False


def test_unknown_kind_rejected(tmp_path):
    path = _write(tmp_path, "d.sql", VALID_SOURCE_SQL)
    assert validate_upload(path, "everything")["ok"] is False


# --- listing -----------------------------------------------------------------

def test_list_target_backups_newest_first(tmp_path):
    older = _write(tmp_path, "lrmis_target-20260101-000000.sql", "-- MySQL dump\n")
    newer = _write(tmp_path, "lrmis_target-20260201-000000.sql", "-- MySQL dump\n")
    base = os.stat(newer).st_mtime
    os.utime(older, (base - 3600, base - 3600))
    _write(tmp_path, "notes.txt", "not a backup")
    backups = list_target_backups(str(tmp_path))
    assert [b["id"] for b in backups] == [
        "lrmis_target-20260201-000000.sql", "lrmis_target-20260101-000000.sql"]
    assert all(b["size_bytes"] > 0 for b in backups)


def test_list_target_backups_missing_dir_is_empty():
    assert list_target_backups("does/not/exist") == []


# --- fake central ------------------------------------------------------------

class _FakeCentral:
    """Just enough of PostgresCentralConnector for the recovery service."""

    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.executed = []
        self.inserted = None
        self.committed = False

    def connection(self):
        central = self

        class _Cur:
            def __enter__(cur):
                return cur

            def __exit__(cur, *a):
                return False

            def execute(cur, sql, params=None):
                central.executed.append((sql, params))
                cur._sql, cur._params = sql, params

            def fetchone(cur):
                if "INSERT INTO integration.recovery_upload" in cur._sql:
                    (kind, fn, path, checksum, size, valid, reason, by) = cur._params
                    central.inserted = {
                        "id": 1, "kind": kind, "original_filename": fn,
                        "stored_path": path, "checksum": checksum,
                        "size_bytes": size, "valid": valid,
                        "invalid_reason": reason, "uploaded_by": by,
                        "used_at": None, "used_by": None}
                    return central.inserted
                if "FROM integration.recovery_upload" in cur._sql:
                    return central.rows.get(cur._params[0])
                return None

        class _Conn:
            def __enter__(conn):
                return conn

            def __exit__(conn, *a):
                return False

            def cursor(conn, cursor_factory=None):
                return _Cur()

            def commit(conn):
                central.committed = True

        return _Conn()

    def close(self):
        pass


# --- stage_upload ------------------------------------------------------------

def test_stage_upload_quarantines_validates_and_records(tmp_path):
    central = _FakeCentral()
    row = stage_upload(io.BytesIO(VALID_SOURCE_SQL.encode("utf-8")),
                       "../evil/../lrmis dump.sql", "source_dump", "admin",
                       central=central, upload_dir=str(tmp_path / "uploads"))
    assert row["valid"] is True and row["validation"]["ok"] is True
    assert row["kind"] == "source_dump" and row["uploaded_by"] == "admin"
    # quarantined + sanitized: inside the upload dir, no path traversal
    assert os.path.dirname(row["stored_path"]) == str(tmp_path / "uploads")
    assert ".." not in os.path.basename(row["stored_path"])
    assert os.path.exists(row["stored_path"])
    assert len(row["checksum"]) == 64
    assert central.committed is True


def test_stage_upload_records_invalid_files_with_reason(tmp_path):
    central = _FakeCentral()
    row = stage_upload(io.BytesIO(VALID_SOURCE_SQL.encode("utf-16")),
                       "dump.sql", "source_dump", "admin",
                       central=central, upload_dir=str(tmp_path / "uploads"))
    assert row["valid"] is False
    assert "UTF-16" in row["invalid_reason"]
    # invalid uploads are still recorded (audited) and kept for inspection
    assert os.path.exists(row["stored_path"])


def test_stage_upload_rejects_unknown_kind(tmp_path):
    with pytest.raises(ValidationError):
        stage_upload(io.BytesIO(b"x"), "d.sql", "nope", "admin",
                     central=_FakeCentral(), upload_dir=str(tmp_path))


# --- restore_target ----------------------------------------------------------

def _upload_row(id, kind, path, valid=True, reason=None):
    return {"id": id, "kind": kind, "stored_path": path, "valid": valid,
            "invalid_reason": reason, "original_filename": os.path.basename(path)}


def test_restore_target_requires_typed_confirmation(tmp_path):
    calls = []
    with pytest.raises(ValidationError, match="typed confirmation"):
        restore_target("some-backup.sql", confirm="wrong", by="admin",
                       central=_FakeCentral(), backup_dir=str(tmp_path),
                       runner=lambda cmd: calls.append(cmd))
    assert calls == []


def test_restore_target_from_listed_backup_file(tmp_path):
    path = _write(tmp_path, "lrmis_target-20260101-000000.sql", VALID_TARGET_SQL)
    calls = []
    result = restore_target(
        "lrmis_target-20260101-000000.sql", confirm="lrmis_target", by="admin",
        central=_FakeCentral(), backup_dir=str(tmp_path),
        runner=lambda cmd: calls.append(cmd) or {"executed": True, "returncode": 0})
    assert result["executed"] is True
    assert calls and path in calls[0] and "lrmis_target" in calls[0]


def test_restore_target_dry_run_executes_nothing(tmp_path):
    _write(tmp_path, "b.sql", VALID_TARGET_SQL)
    calls = []
    result = restore_target("b.sql", confirm="lrmis_target", by="admin",
                            central=_FakeCentral(), backup_dir=str(tmp_path),
                            dry_run=True, runner=lambda cmd: calls.append(cmd))
    assert result["executed"] is False and calls == []


def test_restore_target_from_validated_upload_marks_used(tmp_path):
    path = _write(tmp_path, "up.sql", VALID_TARGET_SQL)
    central = _FakeCentral(rows={7: _upload_row(7, "target_backup", path)})
    result = restore_target("7", confirm="lrmis_target", by="admin",
                            central=central,
                            runner=lambda cmd: {"executed": True, "returncode": 0})
    assert result["executed"] is True
    used = [sql for sql, _ in central.executed if "used_at" in sql]
    assert used and central.committed is True


def test_restore_target_refuses_invalid_upload(tmp_path):
    path = _write(tmp_path, "bad.sql", VALID_TARGET_SQL)
    central = _FakeCentral(rows={7: _upload_row(
        7, "target_backup", path, valid=False, reason="file is UTF-16")})
    with pytest.raises(ValidationError, match="failed validation"):
        restore_target("7", confirm="lrmis_target", by="admin", central=central,
                       runner=lambda cmd: pytest.fail("must not execute"))


def test_restore_target_refuses_wrong_kind_upload(tmp_path):
    path = _write(tmp_path, "s.sql", VALID_SOURCE_SQL)
    central = _FakeCentral(rows={7: _upload_row(7, "source_dump", path)})
    with pytest.raises(ValidationError, match="not a target_backup"):
        restore_target("7", confirm="lrmis_target", by="admin", central=central,
                       runner=lambda cmd: pytest.fail("must not execute"))


def test_restore_target_refuses_empty_backup_file(tmp_path):
    """Found by the live E2E smoke: a 0-byte nightly backup (mysqldump was
    missing when backup_target ran) restored as a silent no-op. Now refused."""
    _write(tmp_path, "empty-backup.sql", b"")
    with pytest.raises(ValidationError, match="file is empty"):
        restore_target("empty-backup.sql", confirm="lrmis_target", by="admin",
                       central=_FakeCentral(), backup_dir=str(tmp_path),
                       runner=lambda cmd: pytest.fail("must not execute"))


def test_restore_target_refuses_non_sql_backup_file(tmp_path):
    _write(tmp_path, "junk.sql", b"\x89PNG\r\n\x1a\n" + os.urandom(64))
    with pytest.raises(ValidationError, match="failed validation"):
        restore_target("junk.sql", confirm="lrmis_target", by="admin",
                       central=_FakeCentral(), backup_dir=str(tmp_path),
                       runner=lambda cmd: pytest.fail("must not execute"))


def test_restore_target_missing_file_not_found(tmp_path):
    with pytest.raises(NotFoundError):
        restore_target("nope.sql", confirm="lrmis_target", by="admin",
                       central=_FakeCentral(), backup_dir=str(tmp_path),
                       runner=lambda cmd: pytest.fail("must not execute"))


def test_restore_target_backup_id_cannot_escape_backup_dir(tmp_path):
    outside = _write(tmp_path, "outside.sql", VALID_TARGET_SQL)
    backups = tmp_path / "backups"
    backups.mkdir()
    with pytest.raises(NotFoundError):
        restore_target(f"../{os.path.basename(outside)}", confirm="lrmis_target",
                       by="admin", central=_FakeCentral(),
                       backup_dir=str(backups),
                       runner=lambda cmd: pytest.fail("must not execute"))


# --- restore_source ----------------------------------------------------------

def test_restore_source_requires_typed_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("LRMIS_SOURCE_RESTORE_CMD", "echo {dump}")
    with pytest.raises(ValidationError, match="typed confirmation"):
        restore_source(7, confirm="wrong", by="admin", central=_FakeCentral(),
                       runner=lambda cmd: pytest.fail("must not execute"))


def test_restore_source_runs_configured_command_and_marks_used(tmp_path, monkeypatch):
    monkeypatch.setenv("LRMIS_SOURCE_RESTORE_CMD", "restore-tool {dump}")
    path = _write(tmp_path, "up.sql", VALID_SOURCE_SQL)
    central = _FakeCentral(rows={9: _upload_row(9, "source_dump", path)})
    calls = []
    result = restore_source(9, confirm="irimsv", by="admin", central=central,
                            runner=lambda cmd: calls.append(cmd) or
                            {"executed": True, "returncode": 0})
    assert result["executed"] is True
    assert calls == [f"restore-tool {path}"]
    used = [sql for sql, _ in central.executed if "used_at" in sql]
    assert used and central.committed is True


def test_restore_source_dry_run_marks_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("LRMIS_SOURCE_RESTORE_CMD", "restore-tool {dump}")
    path = _write(tmp_path, "up.sql", VALID_SOURCE_SQL)
    central = _FakeCentral(rows={9: _upload_row(9, "source_dump", path)})
    result = restore_source(9, confirm="irimsv", by="admin", central=central,
                            dry_run=True,
                            runner=lambda cmd: pytest.fail("must not execute"))
    assert result["executed"] is False
    assert not [sql for sql, _ in central.executed if "used_at" in sql]


def test_restore_source_refuses_invalid_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("LRMIS_SOURCE_RESTORE_CMD", "restore-tool {dump}")
    path = _write(tmp_path, "up.sql", VALID_SOURCE_SQL)
    central = _FakeCentral(rows={9: _upload_row(
        9, "source_dump", path, valid=False, reason="file is UTF-16")})
    with pytest.raises(ValidationError, match="failed validation"):
        restore_source(9, confirm="irimsv", by="admin", central=central,
                       runner=lambda cmd: pytest.fail("must not execute"))


def test_restore_source_unknown_upload(monkeypatch):
    monkeypatch.setenv("LRMIS_SOURCE_RESTORE_CMD", "restore-tool {dump}")
    with pytest.raises(NotFoundError):
        restore_source(404, confirm="irimsv", by="admin", central=_FakeCentral(),
                       runner=lambda cmd: pytest.fail("must not execute"))


def test_safe_filename_strips_paths_and_specials():
    assert _safe_filename("../../etc/passwd") == "passwd"
    assert _safe_filename("my dump (1).sql") == "my_dump__1_.sql"
    assert _safe_filename("") == "upload"
