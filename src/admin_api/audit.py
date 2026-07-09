"""Uniform admin_action_audit writer for every mutating endpoint and job."""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager

from . import db


def write_audit(actor: str, action: str, *, target_type: str | None = None,
                target_id: str | None = None, request_id: str | None = None,
                reason: str | None = None, details: dict | None = None,
                result: str = "success", error_message: str | None = None) -> None:
    with db.central().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO integration.admin_action_audit
                    (actor, action, target_type, target_id, request_id, reason,
                     details, result, error_message)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (actor, action, target_type, target_id,
                  request_id or str(uuid.uuid4()), reason,
                  json.dumps(details, default=str) if details is not None else None,
                  result, error_message))
        conn.commit()


@contextmanager
def audited(actor: str, action: str, *, target_type: str | None = None,
            target_id: str | None = None, reason: str | None = None,
            details: dict | None = None):
    """Audit success or failure of the wrapped block (failure re-raises)."""
    request_id = str(uuid.uuid4())
    try:
        yield request_id
    except Exception as exc:
        write_audit(actor, action, target_type=target_type, target_id=target_id,
                    request_id=request_id, reason=reason, details=details,
                    result="failure", error_message=str(exc))
        raise
    else:
        write_audit(actor, action, target_type=target_type, target_id=target_id,
                    request_id=request_id, reason=reason, details=details,
                    result="success")


def list_audit(limit: int = 200, actor: str | None = None,
               action: str | None = None) -> list[dict]:
    import psycopg2.extras
    clauses, params = [], []
    if actor:
        clauses.append("actor = %s")
        params.append(actor)
    if action:
        clauses.append("action = %s")
        params.append(action)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with db.central().connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT * FROM integration.admin_action_audit {where}
                ORDER BY performed_at DESC LIMIT %s
            """, (*params, limit))
            return [dict(r) for r in cur.fetchall()]
