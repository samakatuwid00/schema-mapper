"""Tests for the guarded Postgres target-restore helper (§0.3)."""
import pytest

from src.services.pg_restore import is_pg_custom_dump, restore_pg_backup
from src.services.common import ValidationError


def _write(tmp_path, name, data: bytes):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_is_pg_custom_dump_detects_magic(tmp_path):
    custom = _write(tmp_path, "old-lrmis.backup", b"PGDMP\x01\x0e\x00...")
    plain = _write(tmp_path, "dump.sql", b"-- PostgreSQL database dump\n")
    assert is_pg_custom_dump(custom) is True
    assert is_pg_custom_dump(plain) is False
    assert is_pg_custom_dump(str(tmp_path / "missing")) is False


def test_dry_run_builds_pg_restore_command(tmp_path):
    backup = _write(tmp_path, "t.backup", b"PGDMP\x01")
    plan = restore_pg_backup(backup_path=backup,
                             dsn="postgresql://u:p@localhost:5432/lrmis_target",
                             dry_run=True)
    assert plan["executed"] is False
    assert plan["is_pg_custom_dump"] is True
    assert "pg_restore" in plan["command"]
    assert backup in plan["command"]
    assert "lrmis_target" in plan["command"]


def test_custom_restore_cmd_is_templated(tmp_path):
    backup = _write(tmp_path, "t.backup", b"PGDMP\x01")
    plan = restore_pg_backup(
        backup_path=backup, dsn="DSN",
        restore_cmd='pg_restore --jobs=4 -d {dsn} {backup}', dry_run=True)
    assert plan["command"] == f"pg_restore --jobs=4 -d DSN {backup}"


def test_refuses_plain_sql_with_default_command(tmp_path):
    plain = _write(tmp_path, "dump.sql", b"-- not a custom archive\n")
    with pytest.raises(ValidationError):
        restore_pg_backup(backup_path=plain, dsn="postgresql://x/y", dry_run=True)


def test_refuses_when_unconfigured(tmp_path, monkeypatch):
    # assert the "no dsn / no restore command" refusal — so the env must actually
    # be unconfigured (.env is auto-loaded at import and may set these).
    monkeypatch.delenv("LRMIS_TARGET_PG_DSN", raising=False)
    monkeypatch.delenv("LRMIS_TARGET_RESTORE_CMD", raising=False)
    backup = _write(tmp_path, "t.backup", b"PGDMP\x01")
    with pytest.raises(ValidationError):
        restore_pg_backup(backup_path=backup, dsn=None, dry_run=True)


def test_missing_backup_raises(tmp_path):
    with pytest.raises(ValidationError):
        restore_pg_backup(backup_path=str(tmp_path / "nope.backup"),
                          dsn="postgresql://x/y", dry_run=True)
