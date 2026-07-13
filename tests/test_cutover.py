"""Tests for the retire-legacy-staging migration gate (§0)."""
from contextlib import contextmanager

from src.services.cutover import summarize, precheck


def _e(name, status, target=None):
    return {"id": 0, "source_table": name, "status": status,
            "lrmis_target_tables": target}


def test_summarize_ready_when_no_delivering_legacy_entity():
    rows = [_e("a", "deployed", ["x"]),            # on target
            _e("b", "paused", ["y"]),              # on target (paused)
            _e("c", "discovered"),                 # legacy-only but never onboarded -> not blocking
            _e("d", "disabled")]                   # retired -> not blocking
    r = summarize(rows)
    assert r["ready"] is True
    assert r["blocking"] == [] and r["on_target"] == 2
    assert r["legacy_only_by_status"] == {"discovered": 1, "disabled": 1}


def test_summarize_blocks_on_paused_and_reviewed_legacy():
    rows = [_e("ok", "deployed", ["x"]),
            _e("legacy_paused", "paused"),         # legacy-only + resumable -> BLOCKS
            _e("legacy_reviewed", "reviewed"),     # legacy-only + reviewed  -> BLOCKS
            _e("retired", "disabled")]             # not blocking
    r = summarize(rows)
    assert r["ready"] is False
    assert sorted(r["blocking"]) == ["legacy_paused", "legacy_reviewed"]
    assert r["blocking_count"] == 2


def test_summarize_empty_is_ready():
    assert summarize([])["ready"] is True


# --- precheck with a fake central ---
class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        pass

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self, cursor_factory=None):
        return _Cur(self._rows)


class _FakeCentral:
    def __init__(self, rows):
        self._rows = rows

    @contextmanager
    def connection(self):
        yield _Conn(self._rows)

    def close(self):
        pass


def test_precheck_reads_central_and_gates():
    rows = [_e("a", "deployed", ["x"]), _e("legacy", "paused")]
    r = precheck(central=_FakeCentral(rows), target_system="LRMIS")
    assert r["ready"] is False and r["blocking"] == ["legacy"]
    assert r["target_system"] == "LRMIS"
