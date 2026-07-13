"""Tests for the MigrationAgent (§8) — schema-only, gated, provider-agnostic.

No live LLM or DB: planning uses an injected `propose`; guide/heal are
deterministic; the audit sink is exercised with a fake cursor.
"""
from src.agent import MigrationAgent, Dilemma, best_match, make_central_audit
from src.mapping_engine import FieldMapping
from src.schema_models import Column, Schema, Table


def _source():
    return Table(name="schools", columns=[
        Column(name="id", data_type="int", nullable=False, is_primary_key=True)])


def _target():
    return Schema(system_name="LRMIS", tables=[
        Table(name="school", columns=[
            Column(name="name", data_type="varchar", nullable=False, is_primary_key=False)])])


def _agent(mappings, **kw):
    return MigrationAgent(propose=lambda st, ts: mappings, **kw)


def test_plan_flags_low_confidence_and_unmapped_and_is_gated():
    fm = [FieldMapping("id", "school", "id", 0.95, "none", "ok"),
          FieldMapping("nm", "school", "name", 0.40, "none", "weak"),
          FieldMapping("x", None, None, 0.0, "none", "no match")]
    plan = _agent(fm, threshold=0.7).plan(_source(), _target())
    assert plan.auto_ok is False
    assert sorted(r.kind for r in plan.risks) == ["low_confidence", "unmapped_column"]
    assert len(plan.low_confidence) == 2


def test_plan_auto_ok_when_all_confident():
    fm = [FieldMapping("id", "school", "id", 0.9, "none", "")]
    assert _agent(fm).plan(_source(), _target()).auto_ok is True


def test_plan_records_audit_as_agent():
    calls = []
    agent = _agent([FieldMapping("id", "school", "id", 0.9, "none", "")],
                   audit=lambda action, details, by: calls.append((action, by)))
    agent.plan(_source(), _target())
    assert ("agent_plan", "agent") in calls


def test_guide_unmapped_column_recommends_best_match():
    g = MigrationAgent().guide(Dilemma(
        kind="unmapped_column", table="school", column="school_name",
        context={"candidates": ["id", "name", "school_name_full"]}))
    assert g.recommended == "auto_suggest"
    auto = next(o for o in g.options if o["action"] == "auto_suggest")
    assert auto["value"] == "school_name_full"


def test_guide_type_mismatch_recommends_cast():
    g = MigrationAgent().guide(Dilemma(kind="type_mismatch", table="school", column="code"))
    assert g.recommended == "cast"


def test_best_match():
    assert best_match("region_id", ["regionid", "name"]) == "regionid"
    assert best_match("code", ["postal_code", "name"]) == "postal_code"
    assert best_match("xyz", ["a", "b"]) is None


def test_heal_is_gated_by_default_and_autonomous_when_enabled():
    a = MigrationAgent()
    p = a.heal("invalid input syntax for type integer: 'abc'")
    assert p.action == "cast" and p.apply is False
    assert MigrationAgent(autonomous_heal=True).heal(
        "invalid input syntax for type integer: 'x'").apply is True
    assert a.heal("violates foreign key constraint").action == "quarantine"


def test_central_audit_inserts_agent_row():
    calls = []

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            calls.append((sql, params))

    class _Conn:
        def cursor(self):
            return _Cur()

    make_central_audit(_Conn(), entity_id=7)("agent_heal", {"x": 1}, "agent")
    sql, params = calls[0]
    assert "onboarding_audit" in sql
    assert params[0] == 7 and params[2] == "agent_heal" and params[4] == "agent"
