"""Embedded migration agent (generic engine, §8).

`MigrationAgent` is the interactive AI layer over the provider-agnostic mapping
engine. It never sees row data — it plans, guides, and heals from schema
metadata and error text only, and every action it proposes is human-gated by
default (a low-confidence plan pauses; a heal is proposed, not auto-applied,
unless autonomous mode is explicitly enabled). It reuses the free-tier
multi-provider `propose_mapping` for the AI part, so it runs on free AI APIs.
"""
from __future__ import annotations

from .agent import (
    MigrationAgent, MigrationPlan, Dilemma, Guidance, HealProposal, Risk,
    best_match,
)
from .audit import make_central_audit

__all__ = [
    "MigrationAgent",
    "MigrationPlan",
    "Dilemma",
    "Guidance",
    "HealProposal",
    "Risk",
    "best_match",
    "make_central_audit",
]
