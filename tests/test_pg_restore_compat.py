import pytest

from scripts import pg_restore_compat


def test_rename_schema_sql_only_runs_when_source_schema_exists():
    sql = pg_restore_compat._rename_schema_sql("lrmis", "irimsv")

    assert "schema_name = 'lrmis'" in sql
    assert 'ALTER SCHEMA "lrmis" RENAME TO "irimsv"' in sql


def test_pre_restore_sql_drops_target_and_source_with_cascade():
    sql = pg_restore_compat._pre_restore_sql("lrmis", "irimsv")

    assert 'DROP SCHEMA IF EXISTS "irimsv" CASCADE;' in sql
    assert 'DROP SCHEMA IF EXISTS "lrmis" CASCADE;' in sql


def test_pre_restore_sql_recreates_the_source_schema_pg_restore_will_not():
    sql = pg_restore_compat._pre_restore_sql("lrmis", "irimsv")

    # `pg_restore --schema=lrmis` emits lrmis's objects but no `CREATE SCHEMA`.
    assert sql.index('DROP SCHEMA IF EXISTS "lrmis" CASCADE;') < sql.index(
        'CREATE SCHEMA "lrmis";')


def test_pre_restore_sql_deduplicates_schema_names():
    sql = pg_restore_compat._pre_restore_sql("irimsv", "irimsv")

    assert sql.count('DROP SCHEMA IF EXISTS "irimsv" CASCADE;') == 1


def test_restore_cmd_restricts_a_renamed_restore_to_a_toc(tmp_path):
    toc = tmp_path / "source.restore.toc"
    cmd = pg_restore_compat._restore_cmd("pg_restore", "source.backup", toc)

    # central owns `integration` / `lrmis_projection`; a whole-database source
    # dump must not replay them into central.
    assert f"--use-list={toc}" in cmd
    assert "--clean" not in cmd


def test_restore_cmd_cleans_when_no_rename_is_requested():
    cmd = pg_restore_compat._restore_cmd("pg_restore", "source.backup", None)

    assert cmd == ["pg_restore", "--no-owner", "--no-privileges", "--clean",
                   "--if-exists", "--file=-", "source.backup"]


def test_rename_schema_sql_rejects_unsafe_names():
    with pytest.raises(ValueError):
        pg_restore_compat._rename_schema_sql("lrmis;drop schema public", "irimsv")


def test_pre_restore_sql_rejects_unsafe_names():
    with pytest.raises(ValueError):
        pg_restore_compat._pre_restore_sql("lrmis;drop schema public", "irimsv")


def _fake_pg_restore_listings(monkeypatch, full: bytes, schema_only: bytes):
    def fake_run(cmd, **kwargs):
        listing = schema_only if any(a.startswith("--schema=") for a in cmd) else full
        return type("R", (), {"returncode": 0, "stdout": listing})()

    monkeypatch.setattr(pg_restore_compat.subprocess, "run", fake_run)


def test_restore_toc_keeps_extensions_and_schema_objects_but_drops_triggers(monkeypatch):
    _fake_pg_restore_listings(
        monkeypatch,
        full=(b"; Archive created at 2026-07-14\n"
              b"2; 3079 17435 EXTENSION - pgcrypto \n"
              b"9; 2615 17472 SCHEMA - integration postgres\n"
              b"4284; 0 0 COMMENT - EXTENSION pgcrypto \n"),
        schema_only=(b"222; 1259 17500 TABLE lrmis authors postgres\n"
                     b"4300; 2620 17600 TRIGGER lrmis authors cdc_authors postgres\n"),
    )

    toc = pg_restore_compat._restore_toc("pg_restore", "source.backup", "lrmis")

    assert b"EXTENSION - pgcrypto" in toc          # lrmis objects depend on it
    assert b"TABLE lrmis authors" in toc
    assert b"TRIGGER" not in toc                   # calls source-only integration.enqueue_*()
    assert b"SCHEMA - integration" not in toc      # central owns its own integration schema


def test_restore_toc_rejects_unsafe_schema_names():
    with pytest.raises(ValueError):
        pg_restore_compat._restore_toc("pg_restore", "source.backup", "lrmis --jobs=9")
