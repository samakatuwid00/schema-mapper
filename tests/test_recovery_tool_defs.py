"""Agent tool-definition tests (source-schema-swap-and-disaster-recovery §6.4).

Schema validation + destructive-gate enforcement, standalone — the registry
(`src/agent/tools.py`, from `conversational-ai-assistant`) has not landed yet;
these pin the contract it will import.
"""
import pytest

from src.agent.tool_defs import (
    RECOVER_FROM_BACKUP, RECOVERY_TOOLS, SWAP_SOURCE_SCHEMA, ToolDef,
    validate_params,
)
from src.schema_models import Schema, Table
from src.services.common import ValidationError


def test_registry_export_contains_both_tools():
    assert [t.name for t in RECOVERY_TOOLS] == [
        "swap_source_schema", "recover_from_backup"]


def test_tooldef_rejects_unknown_autonomy():
    with pytest.raises(ValidationError):
        ToolDef(name="x", description="", params_schema={}, handler=lambda p: p,
                autonomy="yolo")


# --- params schema validation ------------------------------------------------

def test_validate_params_requires_required_keys():
    with pytest.raises(ValidationError, match="missing required param 'action'"):
        validate_params(RECOVER_FROM_BACKUP, {})


def test_validate_params_type_checks():
    with pytest.raises(ValidationError, match="must be boolean"):
        validate_params(SWAP_SOURCE_SCHEMA, {"apply": "yes"})
    assert validate_params(SWAP_SOURCE_SCHEMA,
                           {"apply": True, "threshold": 0.8}) is not None


# --- swap_source_schema gating ------------------------------------------------

class _SpyAdapter:
    engine_type = "postgres"
    schema = "irimsv"

    def __init__(self):
        self.calls = []

    def discover_schema(self):
        self.calls.append("discover_schema")
        return Schema(system_name="IRIMSV", tables=[Table(name="t", columns=[])])


def test_swap_tool_defaults_to_read_only_dry_run():
    adapter = _SpyAdapter()
    result = SWAP_SOURCE_SCHEMA.handler(
        {}, source_adapter=adapter,
        fetch_entities=lambda central, ts: [],
        fetch_source_contracts=lambda central, ts: {})
    assert result["side"] == "source"
    assert "nothing changed" in result["note"]
    assert SWAP_SOURCE_SCHEMA.autonomy == "propose_only"


def test_swap_tool_apply_requires_typed_confirmation():
    with pytest.raises(ValidationError, match="requires confirm='irimsv'"):
        SWAP_SOURCE_SCHEMA.handler(
            {"apply": True}, source_adapter=_SpyAdapter(),
            fetch_entities=lambda central, ts: [],
            fetch_source_contracts=lambda central, ts: {})


def test_swap_tool_apply_with_confirmation_runs_the_gated_apply():
    result = SWAP_SOURCE_SCHEMA.handler(
        {"apply": True, "confirm": "irimsv"}, source_adapter=_SpyAdapter(),
        fetch_entities=lambda central, ts: [],
        fetch_source_contracts=lambda central, ts: {},
        persist=lambda *a: [])
    assert result["status"] == "applied"        # zero affected entities
    assert result["affected_entities"] == []


# --- recover_from_backup destructive gate (design D4) --------------------------

def test_recover_tool_is_hardcoded_destructive():
    assert RECOVER_FROM_BACKUP.autonomy == "destructive"


def test_recover_tool_requires_confirmation_before_anything_runs():
    """The typed-confirm check fires before any DB/shell access, so a missing
    confirmation fails even with no database available at all."""
    with pytest.raises(ValidationError, match="typed confirmation"):
        RECOVER_FROM_BACKUP.handler(
            {"action": "restore_target", "backup_id": "b.sql"})
    with pytest.raises(ValidationError, match="typed confirmation"):
        RECOVER_FROM_BACKUP.handler(
            {"action": "restore_source", "upload_id": 1})


def test_recover_tool_rejects_unknown_action():
    with pytest.raises(ValidationError, match="action must be"):
        RECOVER_FROM_BACKUP.handler({"action": "format_disk"})
