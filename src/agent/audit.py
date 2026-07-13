"""Audit sink for agent actions (§8.7).

Records every agent action in `integration.onboarding_audit` with
`performed_by = 'agent'`. `entity_id`/`proposal_id` are optional (nullable in the
schema) — pass them when the action is scoped to a known entity/proposal.
"""
from __future__ import annotations

import psycopg2.extras


def make_central_audit(central_conn, *, entity_id=None, proposal_id=None):
    """Return an audit sink `(action, details, performed_by) -> None` bound to
    `central_conn`. Commit is the caller's responsibility (it usually shares the
    transaction of whatever the agent just did)."""
    def _sink(action: str, details: dict, performed_by: str) -> None:
        with central_conn.cursor() as cur:
            cur.execute("""
                INSERT INTO integration.onboarding_audit
                    (entity_id, proposal_id, action, details, performed_by)
                VALUES (%s, %s, %s, %s, %s)
            """, (entity_id, proposal_id, action,
                  psycopg2.extras.Json(details or {}), performed_by))
    return _sink
