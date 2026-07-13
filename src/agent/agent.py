"""MigrationAgent — plan, guide, heal (§8.1-8.4, 8.7).

Design constraints (from the proposal):
* **Schema-only** — the agent is fed schema metadata and error text, never row
  values, so it is safe to run on a free AI tier that may train on submissions.
* **Human-gated** — a plan with low-confidence mappings is not `auto_ok`; a heal
  is a *proposal* unless `autonomous_heal=True`.
* **Provider-agnostic** — planning reuses `mapping_engine.propose_mapping`
  (Gemini→Groq→… failover). Guide/heal use deterministic heuristics so they work
  even when no provider is reachable (the free-tier fallback theme).
* **Audited** — if an `audit` sink is given, every action is recorded.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..schema_models import Schema, Table


# --- result types ----------------------------------------------------------

@dataclass
class Risk:
    kind: str            # "unmapped_column" | "low_confidence"
    detail: str


@dataclass
class MigrationPlan:
    source_table: str
    mappings: list[dict]
    risks: list[Risk]
    auto_ok: bool
    low_confidence: list[dict]


@dataclass
class Dilemma:
    kind: str            # "unmapped_column" | "fk_violation" | "type_mismatch"
    table: str
    column: str
    context: dict = field(default_factory=dict)


@dataclass
class Guidance:
    dilemma: Dilemma
    options: list[dict]
    recommended: str     # the recommended option's `action`


@dataclass
class HealProposal:
    error: str
    action: str          # "cast" | "quarantine"
    detail: dict
    apply: bool          # whether the agent will apply it (autonomous mode only)


# --- helpers ---------------------------------------------------------------

def _norm(name: str) -> str:
    return name.lower().replace("_", "").replace(" ", "")


def best_match(name: str, candidates: list[str]) -> str | None:
    """Closest candidate column by normalised name; None if nothing plausible.
    Prefers an exact match, then a candidate that *contains* the whole name
    (e.g. school_name → school_name_full), then one contained in the name."""
    n = _norm(name)
    if not n:
        return None
    for c in candidates:
        if _norm(c) == n:
            return c
    for c in candidates:
        if n in _norm(c):
            return c
    for c in candidates:
        if _norm(c) and _norm(c) in n:
            return c
    return None


def _propose_heal(error: str, context: dict) -> HealProposal:
    """Heuristic fix for a delivery error. Conservative: only the well-understood
    string→int type mismatch is a `cast`; everything else is quarantined."""
    e = (error or "").lower()
    numeric_markers = ("invalid input syntax for type integer", "incorrect integer value",
                       "invalid literal for int", "expected integer")
    if any(m in e for m in numeric_markers) or context.get("expected") == "integer":
        return HealProposal(error=error, action="cast",
                            detail={"transform": "cast:str->int"}, apply=False)
    return HealProposal(error=error, action="quarantine", detail={}, apply=False)


# --- agent -----------------------------------------------------------------

class MigrationAgent:
    def __init__(self, *, threshold: float = 0.7, propose=None,
                 autonomous_heal: bool = False, audit=None):
        self.threshold = threshold
        self._propose = propose
        self.autonomous_heal = autonomous_heal
        self._audit = audit          # callable(action, details, performed_by)

    def _record(self, action: str, details: dict) -> None:
        if self._audit:
            self._audit(action, details, "agent")

    # 8.1 -- planner (AI, schema-only) --------------------------------------
    def plan(self, source_table: Table, target_schema: Schema) -> MigrationPlan:
        """Propose a mapping and grade it: risks + whether it can auto-deploy."""
        if self._propose is None:
            from ..mapping_engine import propose_mapping as propose
        else:
            propose = self._propose
        mappings = propose(source_table, target_schema)

        risks: list[Risk] = []
        low: list = []
        for m in mappings:
            if m.target_table is None or m.target_column is None:
                risks.append(Risk("unmapped_column",
                                  f"{m.source_column} has no confident target"))
                low.append(m)
            elif m.confidence < self.threshold:
                risks.append(Risk("low_confidence",
                                  f"{m.source_column} → {m.target_table}.{m.target_column} "
                                  f"@ {m.confidence:.2f}"))
                low.append(m)

        plan = MigrationPlan(
            source_table=source_table.name,
            mappings=[asdict(m) for m in mappings],
            risks=risks, auto_ok=not low,
            low_confidence=[asdict(m) for m in low])
        self._record("agent_plan", {"source_table": source_table.name,
                                    "risk_kinds": [r.kind for r in risks],
                                    "auto_ok": plan.auto_ok})
        return plan

    # 8.2 -- interactive guide ----------------------------------------------
    def guide(self, dilemma: Dilemma) -> Guidance:
        """Present resolution options for a blocked deploy. The caller (an
        interactive session) picks one; the agent only proposes and recommends."""
        options: list[dict] = []
        recommended = "manual"
        if dilemma.kind == "unmapped_column":
            candidates = dilemma.context.get("candidates", [])
            suggestion = best_match(dilemma.column, candidates)
            if suggestion:
                options.append({"action": "auto_suggest", "value": suggestion,
                                "label": f"map to {dilemma.table}.{suggestion}"})
                recommended = "auto_suggest"
            options.append({"action": "skip", "label": "leave this column unmapped"})
            options.append({"action": "manual", "label": "enter a target column yourself"})
        elif dilemma.kind == "type_mismatch":
            options = [{"action": "cast", "value": "cast:str->int",
                        "label": "cast the source value"},
                       {"action": "skip", "label": "quarantine the row"}]
            recommended = "cast"
        else:  # fk_violation / unknown
            options = [{"action": "quarantine", "label": "quarantine until the parent exists"},
                       {"action": "manual", "label": "resolve manually"}]
            recommended = "quarantine"

        self._record("agent_guide", {"kind": dilemma.kind, "table": dilemma.table,
                                     "column": dilemma.column, "recommended": recommended})
        return Guidance(dilemma=dilemma, options=options, recommended=recommended)

    # 8.3 -- gated self-heal ------------------------------------------------
    def heal(self, error: str, context: dict | None = None) -> HealProposal:
        """Propose a fix for a delivery error. Only applied automatically when
        `autonomous_heal` is on AND the fix is the safe `cast`; otherwise it is a
        proposal the human confirms."""
        proposal = _propose_heal(error, context or {})
        proposal.apply = self.autonomous_heal and proposal.action == "cast"
        self._record("agent_heal", {"action": proposal.action, "apply": proposal.apply,
                                    "detail": proposal.detail})
        return proposal
