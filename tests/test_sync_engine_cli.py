"""Tests for the unified sync-engine CLI dispatcher (§10)."""
import json
import os
import subprocess
import sys

_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "sync_engine.py")


def _run(args):
    return subprocess.run([sys.executable, _SCRIPT, *args],
                          capture_output=True, text=True)


def test_init_writes_config_template(tmp_path):
    cfg = tmp_path / "engine.json"
    r = _run(["init", str(cfg)])
    assert r.returncode == 0
    data = json.loads(cfg.read_text())
    assert data["target"]["engine"] == "postgres"
    assert "cdc" in data and "llm" in data


def test_init_refuses_to_overwrite(tmp_path):
    cfg = tmp_path / "engine.json"
    cfg.write_text("{}")
    assert _run(["init", str(cfg)]).returncode == 1


def test_unknown_command_exits_2():
    assert _run(["frobnicate"]).returncode == 2


def test_run_is_not_implemented():
    assert _run(["run"]).returncode == 2


def test_help_lists_subcommands():
    r = _run(["--help"])
    assert r.returncode == 0
    for c in ("init", "plan", "schema-swap", "agent", "run"):
        assert c in r.stdout
