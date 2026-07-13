"""Free-tier hardening for the mapping engine (§1.2-1.4, §11.5-11.6).

Heuristic fallback, proposal cache, and the schema-only (PII-safe) prompt — all
without a live API key, so the AI-agentic system is testable offline.
"""
import src.mapping_engine as ME
from src.mapping_engine import (
    propose_mapping, heuristic_mapping, _format_columns,
)
from src.schema_models import Column, Schema, Table


def _source():
    return Table(name="users", columns=[
        Column(name="user_name", data_type="varchar"),
        Column(name="zzz_unmatched", data_type="int")])


def _target():
    return Schema(system_name="LRMIS", tables=[Table(name="account", columns=[
        Column(name="username", data_type="varchar"),
        Column(name="email", data_type="varchar")])])


def test_heuristic_maps_by_name_with_low_confidence():
    m = heuristic_mapping(_source(), _target())
    assert m[0].target_table == "account" and m[0].target_column == "username"
    assert m[0].confidence < 0.7                       # flagged for review
    assert m[1].target_table is None and m[1].confidence == 0.0   # no match


def test_offline_provider_order_uses_heuristic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "heuristic")
    m = propose_mapping(_source(), _target())          # no API key needed
    assert m[0].target_column == "username"


def test_failover_to_heuristic_when_llm_unconfigured(monkeypatch):
    # gemini has no key -> skipped; heuristic is the terminal fallback
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "gemini,heuristic")
    m = propose_mapping(_source(), _target())
    assert m[0].reasoning == "heuristic name match"


def test_cache_skips_recompute_for_same_schema(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "heuristic")
    calls = {"n": 0}
    real = ME.heuristic_mapping

    def counting(src, tgt):
        calls["n"] += 1
        return real(src, tgt)

    monkeypatch.setattr(ME, "heuristic_mapping", counting)
    cache = {}
    a = propose_mapping(_source(), _target(), cache=cache)
    b = propose_mapping(_source(), _target(), cache=cache)
    assert calls["n"] == 1          # second call served from cache
    assert a is b


def test_prompt_is_schema_only_never_row_values():
    # a column carrying sample PII must NOT reach the prompt
    table = Table(name="people", columns=[
        Column(name="ssn", data_type="varchar", sample_values=["123-45-6789"])])
    rendered = _format_columns(table)
    assert "ssn" in rendered and "varchar" in rendered
    assert "123-45-6789" not in rendered


def test_prompt_annotates_generic_type(monkeypatch):
    # §3.5: engine-independent GenericType hint alongside the native type
    table = Table(name="t", columns=[
        Column(name="a", data_type="character varying"),
        Column(name="n", data_type="bigint")])
    rendered = _format_columns(table)
    assert "character varying [string]" in rendered
    assert "bigint [integer]" in rendered


def test_no_provider_and_no_heuristic_still_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "gemini,fallback")
    import pytest
    with pytest.raises(RuntimeError):
        propose_mapping(_source(), _target())
