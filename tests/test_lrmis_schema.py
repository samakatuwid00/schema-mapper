"""Phase 7: LRMIS DDL fingerprinting and drift detection (pure logic)."""
from __future__ import annotations

from contextlib import contextmanager

from src.services import lrmis_schema as S


def test_fingerprint_is_stable_and_content_sensitive(tmp_path):
    f = tmp_path / "ddl.sql"
    f.write_text("CREATE TABLE a (id int);")
    fp1 = S.current_ddl_fingerprint(str(f))
    assert fp1 == S.current_ddl_fingerprint(str(f))     # stable
    f.write_text("CREATE TABLE a (id bigint);")
    assert S.current_ddl_fingerprint(str(f)) != fp1     # content-sensitive


class _Central:
    def __init__(self, stored):
        self._stored = stored

    @contextmanager
    def connection(self):
        yield _Conn(self._stored)

    def close(self):
        pass


class _Conn:
    def __init__(self, stored):
        self._stored = stored

    def cursor(self, *a, **k):
        stored = self._stored

        class _C:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def execute(self_, sql, params=()):
                self_._row = (stored,) if stored is not None else None

            def fetchone(self_):
                return self_._row
        return _C()


def test_drift_unversioned_when_nothing_stored(tmp_path):
    f = tmp_path / "d.sql"
    f.write_text("x")
    out = S.check_ddl_drift(central=_Central(stored=None), path=str(f))
    assert out["unversioned"] is True
    assert out["drifted"] is False


def test_drift_false_when_stored_matches(tmp_path):
    f = tmp_path / "d.sql"
    f.write_text("x")
    current = S.current_ddl_fingerprint(str(f))
    out = S.check_ddl_drift(central=_Central(stored=current), path=str(f))
    assert out["drifted"] is False
    assert out["unversioned"] is False


def test_drift_true_when_stored_differs(tmp_path):
    f = tmp_path / "d.sql"
    f.write_text("x")
    out = S.check_ddl_drift(central=_Central(stored="deadbeef"), path=str(f))
    assert out["drifted"] is True
