"""Read-only operator diagnostics for the conversational assistant.

These helpers summarize job, deployment, and mapping state without returning
source or target row values. They are intentionally service-level so the chat
agent can answer real operational questions without learning database SQL.
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any

import psycopg2.extras

from ..connectors import PostgresCentralConnector
from .common import NotFoundError, ValidationError
from .targets import configured_target

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.I,
)
_MISSING_REQUIRED_RE = re.compile(
    r"-\s*([a-z_][\w]*)\.([a-z_][\w]*)\s+is required but no source column maps to it",
    re.I,
)
_PG_DUPLICATE_RE = re.compile(
    r'unique constraint "([a-z_][\w]*)_pkey".*?Key \(([a-z_][\w]*)\)=\(([^)]+)\) already exists',
    re.I | re.S,
)
_MYSQL_DUPLICATE_RE = re.compile(
    r"Duplicate entry '([^']+)' for key '(?:[a-z_][\w]*\.)?(?:PRIMARY|([a-z_][\w]*)_pkey)'",
    re.I,
)
_READ_ONLY_REFERENCE_RE = re.compile(
    r"([a-z_][\w]*)\.([a-z_][\w]*)=(?:'([^']+)'|([^;\s]+)) does not exist;\s*"
    r"\1 is read-only",
    re.I,
)
_REFERENCE_MATCH_RE = re.compile(
    r"no row in reference table\s+([a-z_][\w]*)\s+matches\s+(\{[^}]*\})",
    re.I,
)
_BAD_DATE_RE = re.compile(
    r"([a-z_][\w]*)\.([a-z_][\w]*) date year ([0-9]+) is outside supported range",
    re.I,
)


def _loads(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _safe_job_params(params: Any) -> dict:
    params = _loads(params)
    if not isinstance(params, dict):
        return {}
    return {k: v for k, v in params.items() if not str(k).startswith("_")}


def _failures_from_result(result: Any) -> list[dict]:
    result = _loads(result)
    if not isinstance(result, dict):
        return []
    failures: list[dict] = []
    for key in ("results", "entities", "redeliver"):
        rows = result.get(key)
        if isinstance(rows, dict):
            rows = rows.get("results") or rows.get("entities")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "").lower()
            if status in ("failed", "skipped", "error") or row.get("error"):
                failures.append({
                    "entity": row.get("entity") or row.get("table") or row.get("source_table"),
                    "status": row.get("status") or "failed",
                    "error": row.get("error"),
                })
    return failures


def _job_summary(row: dict, events: list[dict] | None = None) -> dict:
    result = _loads(row.get("result"))
    return {
        "job_id": str(row["id"]),
        "job_type": row.get("job_type"),
        "status": row.get("status"),
        "params": _safe_job_params(row.get("params")),
        "progress_current": row.get("progress_current"),
        "progress_total": row.get("progress_total"),
        "requested_by": row.get("requested_by"),
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "error_message": row.get("error_message"),
        "result_summary": _summarize_result(result),
        "failures": _failures_from_result(result),
        "events": [
            {
                "event_type": e.get("event_type"),
                "message": e.get("message"),
                "data": _loads(e.get("data")),
                "created_at": e.get("created_at"),
            }
            for e in (events or [])[-5:]
        ],
    }


def _summarize_result(result: Any) -> dict:
    result = _loads(result)
    if not isinstance(result, dict):
        return {}
    summary = {}
    for key in ("requested", "deployed", "failed", "proposed", "skipped",
                "written", "duration_seconds"):
        if key in result:
            summary[key] = result[key]
    if "results" in result and isinstance(result["results"], list):
        summary["result_count"] = len(result["results"])
        summary["failed_count"] = len(_failures_from_result(result))
    if "steps" in result and isinstance(result["steps"], dict):
        summary["steps"] = sorted(result["steps"])
    return summary


def inspect_job(job_id: str | None = None,
                central: PostgresCentralConnector | None = None) -> dict:
    """Return a compact status summary for a job id, or the latest job."""
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if job_id:
                    cur.execute(
                        "SELECT * FROM integration.admin_job WHERE id = %s",
                        (job_id,),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM integration.admin_job "
                        "ORDER BY created_at DESC LIMIT 1"
                    )
                row = cur.fetchone()
                if not row:
                    raise NotFoundError(
                        f"job {job_id} not found" if job_id else "no jobs found"
                    )
                cur.execute("""
                    SELECT event_type, message, data, created_at
                    FROM integration.admin_job_event
                    WHERE job_id = %s
                    ORDER BY id DESC LIMIT 5
                """, (row["id"],))
                events = list(reversed([dict(e) for e in cur.fetchall()]))
        return _job_summary(dict(row), events)
    finally:
        if owns:
            central.close()


def diagnose_entity_delivery(entity: str, target_system: str = "LRMIS",
                             central: PostgresCentralConnector | None = None,
                             target=None) -> dict:
    """Explain whether a deployed entity has reached the target tables."""
    if not entity:
        raise ValidationError("entity is required")
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT e.*, c.enabled AS control_enabled,
                           c.paused_reason AS control_paused_reason,
                           p.id AS latest_proposal_id,
                           p.status AS latest_proposal_status
                    FROM integration.onboarding_entity e
                    LEFT JOIN integration.entity_control c
                      ON c.source_entity = e.source_table
                     AND c.target_system = e.target_system
                    LEFT JOIN LATERAL (
                        SELECT id, status FROM integration.onboarding_proposal p
                        WHERE p.entity_id = e.id
                        ORDER BY created_at DESC LIMIT 1
                    ) p ON true
                    WHERE e.source_table = %s AND e.target_system = %s
                    ORDER BY e.updated_at DESC LIMIT 1
                """, (entity, target_system))
                row = cur.fetchone()
                if not row:
                    raise NotFoundError(
                        f"entity {entity!r} is not onboarded for {target_system}"
                    )
                cur.execute("""
                    SELECT target_table, COUNT(*) AS crosswalk_count
                    FROM integration.id_crosswalk
                    WHERE source_entity = %s AND target_system = %s
                    GROUP BY target_table ORDER BY target_table
                """, (entity, target_system))
                crosswalk = [dict(r) for r in cur.fetchall()]
                cur.execute("""
                    SELECT * FROM integration.admin_job
                    WHERE (params::text ILIKE %s OR result::text ILIKE %s
                           OR COALESCE(error_message, '') ILIKE %s)
                      AND job_type IN ('refresh_all', 'nightly_refresh',
                                       'bulk_deploy_lrmis', 'onboard_bulk')
                    ORDER BY created_at DESC LIMIT 1
                """, (f"%{entity}%", f"%{entity}%", f"%{entity}%"))
                job = cur.fetchone()

        data = dict(row)
        target_tables = _loads(data.get("lrmis_target_tables")) or []
        if isinstance(target_tables, str):
            target_tables = [target_tables]
        target_counts = _target_counts(target_tables, target=target)
        latest_job = _job_summary(dict(job), []) if job else None
        recommendations = _entity_recommendations(data, target_tables,
                                                  target_counts, latest_job)
        return {
            "entity": entity,
            "target_system": target_system,
            "entity_status": data.get("status"),
            "paused_reason": data.get("paused_reason")
                             or data.get("control_paused_reason"),
            "control_enabled": data.get("control_enabled"),
            "latest_proposal_id": data.get("latest_proposal_id"),
            "latest_proposal_status": data.get("latest_proposal_status"),
            "target_tables": target_tables,
            "target_counts": target_counts,
            "crosswalk": crosswalk,
            "latest_job": latest_job,
            "recommendations": recommendations,
        }
    finally:
        if owns:
            central.close()


def _target_counts(target_tables: list[str], target=None) -> list[dict]:
    if not target_tables:
        return []
    target = target or configured_target()
    counts: list[dict] = []
    for table in target_tables:
        try:
            counts.append({"table": table, "rows": int(target.count_rows(table))})
        except Exception as exc:
            counts.append({"table": table, "error": str(exc)})
    return counts


def _entity_recommendations(data: dict, target_tables: list[str],
                            counts: list[dict],
                            latest_job: dict | None) -> list[str]:
    recommendations: list[str] = []
    if data.get("status") == "paused":
        recommendations.append("resolve or rebaseline the paused entity before retrying delivery")
    if not target_tables:
        recommendations.append("deploy the approved proposal to the LRMIS target first")
    if target_tables and counts and all(int(c.get("rows") or 0) == 0 for c in counts if "rows" in c):
        recommendations.append("run or retry refresh for this entity; deploy only records the mapping contract")
    if latest_job and latest_job.get("failures"):
        recommendations.append("inspect the latest job failure and retry only the failed entity after repair")
    if not recommendations:
        recommendations.append("target data is present or no blocking signal was found")
    return recommendations


def explain_deploy_error(error: str, entity: str | None = None,
                         proposal_id: int | None = None,
                         central: PostgresCentralConnector | None = None) -> dict:
    """Parse deploy validation text into concrete repair suggestions."""
    if not error:
        raise ValidationError("error is required")
    job = None
    job_id = extract_job_id(error)
    if proposal_id is None and job_id:
        try:
            job = inspect_job(job_id, central=central)
            raw_pid = (job.get("params") or {}).get("proposal_id")
            if raw_pid is not None:
                proposal_id = int(raw_pid)
        except Exception:
            job = None
    missing = [
        {"target_table": table, "target_column": column}
        for table, column in _MISSING_REQUIRED_RE.findall(error)
    ]
    suggestions = []
    id_missing = [item for item in missing if item["target_column"] == "id"]
    if id_missing:
        suggestions.append({
            "action": "add_missing_mappings",
            "proposal_id": proposal_id,
            "source_column": "id",
            "mappings": [
                {
                    "source_column": "id",
                    "target_table": item["target_table"],
                    "target_column": item["target_column"],
                    "transform": "none",
                }
                for item in id_missing
            ],
            "reason": (
                "required target id columns can usually be repaired as one "
                "fan-out bundle from the source id; confirmation required"
            ),
        })
    for item in missing:
        table = item["target_table"]
        column = item["target_column"]
        if column == "id":
            suggestions.append({
                "action": "add_mapping",
                "source_column": "id",
                "target_table": table,
                "target_column": column,
                "reason": "required primary-key target column is commonly filled from the source id",
            })
        else:
            suggestions.append({
                "action": "add_mapping",
                "target_table": table,
                "target_column": column,
                "reason": "required target column needs a source column or writer default",
            })
    if "no row in reference table" in error.lower():
        suggestions.append({
            "action": "diagnose_entity_delivery",
            "entity": entity,
            "reason": "a referenced parent table may be missing data or mapped to the wrong target table",
        })
    if "mapping cannot be deployed" in error.lower() and not suggestions:
        suggestions.append({
            "action": "summarize_proposal",
            "proposal_id": proposal_id,
            "reason": "review accepted/resolved mappings and unmet required columns",
        })
    duplicate = parse_duplicate_key(error)
    if duplicate:
        suggestions.append({
            "action": "diagnose_duplicate_key",
            "entity": entity,
            **duplicate,
            "reason": "target primary key already exists; reconcile ownership before retry",
        })
    return {
        "entity": entity,
        "proposal_id": proposal_id,
        "job": job,
        "missing_required": missing,
        "suggested_actions": suggestions,
        "summary": (
            f"{len(missing)} required target column(s) are unmapped."
            if missing else
            "No standard missing-required-column pattern was found."
        ),
    }


def extract_job_id(text: str) -> str | None:
    match = _UUID_RE.search(text or "")
    return match.group(0) if match else None


def _proposal_has_source_column(proposal_id: int, column: str,
                                central: PostgresCentralConnector | None = None,
                                ) -> bool:
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 1 FROM integration.onboarding_field_review
                    WHERE proposal_id = %s AND source_column = %s
                      AND status != 'rejected'
                    LIMIT 1
                """, (proposal_id, column))
                return cur.fetchone() is not None
    finally:
        if owns:
            central.close()


def _draft_missing_mapping_repairs(proposal_id: int | None, missing: list[dict],
                                   central: PostgresCentralConnector | None = None,
                                   ) -> tuple[list[dict], list[dict]]:
    """Split missing required target columns into confirmation-gated `*.id`
    fan-out drafts (only when the proposal has a source `id` column) and
    columns that still need a manual source-column choice (design D2)."""
    if not missing:
        return [], []
    if not proposal_id:
        return [], list(missing)
    has_id_source = _proposal_has_source_column(proposal_id, "id", central=central)
    drafts: list[dict] = []
    manual: list[dict] = []
    for item in missing:
        if item.get("target_column") == "id" and has_id_source:
            drafts.append({"source_column": "id",
                           "target_table": item["target_table"],
                           "target_column": item["target_column"]})
        else:
            manual.append(item)
    return drafts, manual


def resolve_deploy_job_repair(text: str, target_system: str = "LRMIS",
                              central: PostgresCentralConnector | None = None,
                              target=None) -> dict:
    """Resolve a pasted failed-deploy job message into a repair context
    (design D1/D2): recovers the job's stored `proposal_id` instead of asking
    the operator to find it, parses missing required target columns from the
    job's error text, and drafts confirmation-gated `*.id` fan-out mappings
    where the proposal has a source `id` column. Read-only."""
    if not text:
        raise ValidationError("text is required")
    job_id = extract_job_id(text)
    job: dict | None = None
    proposal_id: int | None = None
    owns = central is None
    central = central or PostgresCentralConnector()
    if job_id:
        try:
            with central.connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT * FROM integration.admin_job WHERE id = %s",
                        (job_id,))
                    row = cur.fetchone()
            if row:
                params = _loads(row.get("params"))
                if isinstance(params, dict):
                    raw_pid = params.get("proposal_id")
                    proposal_id = int(raw_pid) if raw_pid is not None else None
                job = _job_summary(dict(row))

        except Exception:
            job = None

    try:
        error_text = (job.get("error_message") if job else None) or text
        parsed = explain_deploy_error(error_text, proposal_id=proposal_id,
                                      central=central)
        drafts, manual = _draft_missing_mapping_repairs(
            proposal_id, parsed["missing_required"], central=central)

        actions: list[dict] = []
        if proposal_id:
            actions.append({"type": "open_proposal", "proposal_id": proposal_id})
        if drafts and proposal_id:
            actions.append({
                "type": "gated_repair", "tool": "add_missing_mappings",
                "params": {"proposal_id": proposal_id, "mappings": drafts},
                "label": f"Add {len(drafts)} missing id mapping(s)",
            })

        return {
            "job_id": job_id,
            "job": job,
            "proposal_id": proposal_id,
            "proposal_recovered": proposal_id is not None,
            "missing_required": parsed["missing_required"],
            "draft_mappings": drafts,
            "manual_required": manual,
            "actions": actions,
            "summary": parsed["summary"],
        }
    finally:
        if owns:
            central.close()


def plan_refresh_failure_repair(job_id: str | None = None,
                                target_system: str = "LRMIS",
                                central: PostgresCentralConnector | None = None,
                                target=None) -> dict:
    """Build a read-only repair checklist for failed entities in a refresh job."""
    job = inspect_job(job_id, central=central)
    failures = job.get("failures") or []
    items = [
        _plan_failure_item(failure, target_system=target_system,
                           central=central, target=target)
        for failure in failures
    ]
    if not failures and job.get("status") == "succeeded":
        summary = "No failed entities were reported by this refresh job."
    else:
        safe_actions = sum(
            len(item.get("gated_tools") or ([] if not item.get("gated_tool") else [item["gated_tool"]]))
            for item in items)
        manual = sum(1 for item in items
                     if not item.get("gated_tool") and not item.get("gated_tools"))
        summary = (f"{len(items)} failed entity repair item(s): "
                   f"{safe_actions} gated auto-repair candidate(s), "
                   f"{manual} manual review item(s).")
    return {
        "job": job,
        "target_system": target_system,
        "items": items,
        "summary": summary,
        "read_only": True,
    }


def _plan_failure_item(failure: dict, *, target_system: str,
                       central: PostgresCentralConnector | None,
                       target) -> dict:
    entity = failure.get("entity") or failure.get("table")
    error = str(failure.get("error") or "")
    base = {"entity": entity, "error": error, "category": "unknown",
            "diagnosis": "Refresh failed; inspect the entity mapping and latest job events.",
            "next_steps": ["inspect the entity mapping and retry only this entity after repair"],
            "gated_tool": None}

    duplicate = parse_duplicate_key(error)
    if duplicate and entity:
        base["category"] = "duplicate_key"
        try:
            diag = diagnose_duplicate_key(
                str(entity), error=error, target_system=target_system,
                central=central, target=target)
        except Exception as exc:
            base["diagnosis"] = f"Duplicate key detected, but safety diagnosis failed: {exc}"
            base["next_steps"] = ["review the target id ownership and mapping manually"]
            return base
        base["diagnostic"] = diag
        if diag.get("safe_to_repair"):
            base["diagnosis"] = (
                "Target row already exists and no conflicting crosswalk owns it; "
                "the missing crosswalk can be recorded after confirmation.")
            base["next_steps"] = [
                "approve repair_duplicate_key",
                f"retry refresh for {entity}",
            ]
            base["gated_tool"] = {
                "tool": "repair_duplicate_key",
                "params": {
                    "entity": entity,
                    "error": error,
                    "target_system": target_system,
                },
            }
        else:
            reasons = diag.get("reasons") or ["manual review required"]
            base["diagnosis"] = "Duplicate key is not safe to auto-repair: " + "; ".join(map(str, reasons))
            review = diag.get("mapping_review") or {}
            base["next_steps"] = [
                _review_step(review, "review the accepted mapping for the duplicate target primary key"),
                _reject_review_step(review),
                f"retry refresh for {entity} after mapping/reference repair",
            ]
            if review.get("review_id"):
                base["gated_tools"] = [{
                    "tool": "reject_mapping_review",
                    "params": {"review_id": review["review_id"]},
                    "reason": "reject only this bad target mapping row, not every mapping from the same source column",
                }]
        return base

    ref_match = parse_reference_match_error(error)
    if ref_match:
        base["category"] = "reference_match_missing"
        review = _mapping_review_for_target(
            str(entity), target_system, ref_match["target_table"],
            ref_match["target_column"], central=central) if entity else None
        suspects = _suspect_reference_reviews(
            str(entity), target_system, ref_match, central=central) if entity else []
        base["diagnostic"] = ref_match
        if review:
            base["diagnostic"] = {**ref_match, "mapping_review": review}
        elif suspects:
            base["diagnostic"] = {**ref_match, "suspect_mapping_reviews": suspects}
        base["diagnosis"] = (
            f"No row in reference table {ref_match['target_table']} matches "
            f"{ref_match['target_column']}={ref_match['target_id']!r}.")
        if suspects and not review:
            suspect_text = "; ".join(
                _review_step(s, "review suspect mapping") for s in suspects[:3]
            )
            base["next_steps"] = [
                f"send suspect mapping back to the review queue: {suspect_text}",
                "correct the mapping in Review Queue, then redeploy the proposal",
                f"retry refresh for {entity} after the proposal is approved",
            ]
            base["gated_tools"] = [{
                "tool": "reopen_mapping_review",
                "params": {"review_id": s["review_id"]},
                "reason": "return this suspect accepted mapping to the normal review queue for correction",
            } for s in suspects[:3] if s.get("review_id")]
        else:
            base["next_steps"] = [
                _review_step(review, "review the accepted mapping for the missing reference lookup"),
                _reject_review_step(review),
                "seed/fix the referenced target row outside the delivery flow if the id is valid",
                f"retry refresh for {entity} after mapping/reference repair",
            ]
        if review and review.get("review_id"):
            base["gated_tools"] = [{
                "tool": "reopen_mapping_review",
                "params": {"review_id": review["review_id"]},
                "reason": "return this exact reference mapping to the normal review queue for correction",
            }]
        return base

    ref = parse_read_only_reference_error(error)
    if ref:
        base["category"] = "read_only_reference_missing"
        review = _mapping_review_for_target(
            str(entity), target_system, ref["target_table"],
            ref["target_column"], central=central) if entity else None
        base["diagnostic"] = ref
        if review:
            base["diagnostic"] = {**ref, "mapping_review": review}
        base["diagnosis"] = (
            f"{ref['target_table']}.{ref['target_column']}={ref['target_id']!r} "
            "is missing from a read-only reference table.")
        base["next_steps"] = [
            _review_step(review, "verify whether the mapping should use a different lookup column or existing target id"),
            "seed/fix the target reference table outside the delivery flow if the id is valid",
            f"retry refresh for {entity} after the reference exists",
        ]
        return base

    missing = _MISSING_REQUIRED_RE.findall(error)
    if missing:
        base["category"] = "missing_required_mapping"
        explained = explain_deploy_error(error, entity=str(entity) if entity else None)
        base["diagnostic"] = explained
        base["diagnosis"] = explained["summary"]
        base["next_steps"] = [
            "add or correct required source-to-target mappings",
            f"retry refresh for {entity} after redeploying the proposal",
        ]
        return base

    bad_dates = parse_bad_date_errors(error)
    if bad_dates:
        base["category"] = "data_quality"
        base["diagnostic"] = {"bad_dates": bad_dates}
        base["diagnosis"] = "Source data contains dates outside the target-supported range."
        base["next_steps"] = [
            "clean or transform the bad source date values",
            "rerun refresh after data cleanup",
        ]
        return base

    return base


def parse_read_only_reference_error(error: str) -> dict | None:
    match = _READ_ONLY_REFERENCE_RE.search(error or "")
    if not match:
        return None
    return {
        "target_table": match.group(1),
        "target_column": match.group(2),
        "target_id": match.group(3) or match.group(4),
    }


def parse_reference_match_error(error: str) -> dict | None:
    match = _REFERENCE_MATCH_RE.search(error or "")
    if not match:
        return None
    try:
        criteria = ast.literal_eval(match.group(2))
    except (SyntaxError, ValueError):
        return None
    if not isinstance(criteria, dict) or not criteria:
        return None
    key, value = next(iter(criteria.items()))
    return {
        "target_table": match.group(1),
        "target_column": str(key),
        "target_id": str(value),
        "criteria": {str(k): str(v) for k, v in criteria.items()},
    }


def parse_bad_date_errors(error: str) -> list[dict]:
    return [
        {"target_table": table, "target_column": column, "year": int(year)}
        for table, column, year in _BAD_DATE_RE.findall(error or "")
    ]


def _review_step(review: dict | None, fallback: str) -> str:
    if not review:
        return fallback
    return (f"review proposal {review.get('proposal_id')} field review "
            f"{review.get('review_id')}: {review.get('source_column')} -> "
            f"{review.get('target_table')}.{review.get('target_column')}")


def _reject_review_step(review: dict | None) -> str:
    if not review:
        return "reject or remap the source column if it should not own that target row"
    return (f"if that target mapping is wrong, ask: reject mapping review "
            f"{review.get('review_id')}")


def parse_duplicate_key(error: str) -> dict | None:
    """Extract target table/column/id from common Postgres/MySQL duplicate text."""
    match = _PG_DUPLICATE_RE.search(error or "")
    if match:
        return {
            "target_table": match.group(1),
            "target_column": match.group(2),
            "target_id": match.group(3),
        }
    match = _MYSQL_DUPLICATE_RE.search(error or "")
    if match:
        return {
            "target_table": match.group(2),
            "target_column": "id",
            "target_id": match.group(1),
        }
    return None


def diagnose_duplicate_key(entity: str, error: str | None = None,
                           target_table: str | None = None,
                           target_column: str | None = None,
                           target_id: str | None = None,
                           target_system: str = "LRMIS",
                           central: PostgresCentralConnector | None = None,
                           target=None) -> dict:
    """Plan a duplicate-key repair without mutating central or target data."""
    parsed = parse_duplicate_key(error or "") or {}
    target_table = target_table or parsed.get("target_table")
    target_column = target_column or parsed.get("target_column") or "id"
    target_id = target_id or parsed.get("target_id")
    if not entity:
        raise ValidationError("entity is required")
    if not target_table or not target_id:
        raise ValidationError("target_table and target_id are required")

    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        entity_row, mapping = _duplicate_repair_context(
            central, entity, target_system, target_table, target_column)
        pk_cols = _loads(entity_row["primary_key_columns"]) or []
        if isinstance(pk_cols, str):
            pk_cols = [pk_cols]
        source_column = mapping.get("source_column") if mapping else None
        mapping_review = _review_view(mapping) if mapping else None
        reasons: list[str] = []
        safe = True
        if len(pk_cols) != 1:
            safe = False
            reasons.append("entity has a composite or unknown primary key")
        elif source_column != pk_cols[0]:
            safe = False
            reasons.append(
                f"{target_table}.{target_column} is mapped from {source_column!r}, not source primary key {pk_cols[0]!r}")

        from .. import pipeline
        external_reference = None
        if safe:
            external_reference = str(pipeline.generate_external_reference(
                entity_row["source_system"], entity_row["source_schema"],
                entity_row["source_table"], [target_id]))

        target_exists = _target_row_exists(target_table, target_column, target_id,
                                           target=target)
        if not target_exists:
            safe = False
            reasons.append("target row does not exist; duplicate error may be stale")

        conflicts = _crosswalk_conflicts(
            central, target_system, target_table, str(target_id),
            entity, external_reference)
        if conflicts:
            safe = False
            reasons.append("target id is already claimed by another crosswalk row")

        return {
            "entity": entity,
            "target_system": target_system,
            "target_table": target_table,
            "target_column": target_column,
            "target_id": str(target_id),
            "source_system": entity_row["source_system"],
            "source_schema": entity_row["source_schema"],
            "source_primary_key": pk_cols,
            "mapped_source_column": source_column,
            "mapping_review": mapping_review,
            "external_reference": external_reference,
            "target_row_exists": target_exists,
            "conflicts": conflicts,
            "safe_to_repair": safe,
            "reasons": reasons,
            "recommended_action": (
                "repair_duplicate_key can record the missing crosswalk, then retry refresh for this entity"
                if safe else
                "manual review required before recording ownership"
            ),
        }
    finally:
        if owns:
            central.close()


def repair_duplicate_key(entity: str, error: str | None = None,
                         target_table: str | None = None,
                         target_column: str | None = None,
                         target_id: str | None = None,
                         target_system: str = "LRMIS",
                         actor: str = "agent",
                         central: PostgresCentralConnector | None = None,
                         target=None) -> dict:
    """Record a missing crosswalk for a safe duplicate-key ownership match."""
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        plan = diagnose_duplicate_key(
            entity, error=error, target_table=target_table,
            target_column=target_column, target_id=target_id,
            target_system=target_system, central=central, target=target)
        if not plan["safe_to_repair"]:
            raise ValidationError(
                "duplicate-key repair is not safe: "
                + "; ".join(plan.get("reasons") or ["unknown reason"]))
        with central.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO integration.id_crosswalk
                        (source_system, source_entity, external_reference,
                         target_system, target_table, target_id, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (source_system, source_entity, external_reference,
                                 target_system, target_table)
                    DO UPDATE SET target_id = EXCLUDED.target_id,
                                  updated_at = now()
                """, (plan["source_system"], plan["entity"],
                      plan["external_reference"], plan["target_system"],
                      plan["target_table"], plan["target_id"]))
                cur.execute("""
                    INSERT INTO integration.onboarding_audit
                        (entity_id, action, details, performed_by)
                    SELECT id, 'duplicate_key_crosswalk_repair', %s, %s
                    FROM integration.onboarding_entity
                    WHERE source_table = %s AND target_system = %s
                    ORDER BY updated_at DESC LIMIT 1
                """, (json.dumps({
                    "target_table": plan["target_table"],
                    "target_column": plan["target_column"],
                    "target_id": plan["target_id"],
                    "external_reference": plan["external_reference"],
                }), actor, plan["entity"], plan["target_system"]))
            conn.commit()
        return {**plan, "applied": True, "actor": actor,
                "next_step": f"retry refresh for {entity}"}
    finally:
        if owns:
            central.close()


def _duplicate_repair_context(central: PostgresCentralConnector, entity: str,
                              target_system: str, target_table: str,
                              target_column: str) -> tuple[dict, dict | None]:
    with central.connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM integration.onboarding_entity
                WHERE source_table = %s AND target_system = %s
                ORDER BY updated_at DESC LIMIT 1
            """, (entity, target_system))
            row = cur.fetchone()
            if not row:
                raise NotFoundError(
                    f"entity {entity!r} is not onboarded for {target_system}")
            cur.execute("""
                SELECT r.id AS review_id, r.proposal_id,
                       r.source_column, r.suggested_target_table,
                       r.suggested_target_column, r.resolved_target_column
                FROM integration.onboarding_field_review r
                WHERE r.proposal_id = (
                    SELECT p.id FROM integration.onboarding_proposal p
                    WHERE p.entity_id = %s AND p.status IN ('approved', 'auto_approved')
                    ORDER BY p.id DESC LIMIT 1
                )
                  AND r.status IN ('accepted', 'resolved')
                  AND r.suggested_target_table = %s
                  AND COALESCE(r.resolved_target_column, r.suggested_target_column) = %s
                ORDER BY r.id DESC LIMIT 1
            """, (row["id"], target_table, target_column))
            mapping = cur.fetchone()
    return dict(row), dict(mapping) if mapping else None


def _mapping_review_for_target(entity: str, target_system: str,
                               target_table: str, target_column: str,
                               central: PostgresCentralConnector | None = None,
                               ) -> dict | None:
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT r.id AS review_id, r.proposal_id,
                           r.source_column, r.suggested_target_table,
                           r.suggested_target_column, r.resolved_target_column
                    FROM integration.onboarding_entity e
                    JOIN integration.onboarding_proposal p ON p.entity_id = e.id
                    JOIN integration.onboarding_field_review r ON r.proposal_id = p.id
                    WHERE e.source_table = %s AND e.target_system = %s
                      AND p.status IN ('approved', 'auto_approved')
                      AND r.status IN ('accepted', 'resolved')
                      AND r.suggested_target_table = %s
                      AND COALESCE(r.resolved_target_column, r.suggested_target_column) = %s
                    ORDER BY p.id DESC, r.id DESC LIMIT 1
                """, (entity, target_system, target_table, target_column))
                row = cur.fetchone()
        return _review_view(dict(row)) if row else None
    finally:
        if owns:
            central.close()


def _mapping_reviews_for_table(entity: str, target_system: str,
                               target_table: str,
                               central: PostgresCentralConnector | None = None,
                               ) -> list[dict]:
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT r.id AS review_id, r.proposal_id,
                           r.source_column, r.suggested_target_table,
                           r.suggested_target_column, r.resolved_target_column
                    FROM integration.onboarding_entity e
                    JOIN integration.onboarding_proposal p ON p.entity_id = e.id
                    JOIN integration.onboarding_field_review r ON r.proposal_id = p.id
                    WHERE e.source_table = %s AND e.target_system = %s
                      AND p.status IN ('approved', 'auto_approved')
                      AND r.status IN ('accepted', 'resolved')
                      AND r.suggested_target_table = %s
                    ORDER BY p.id DESC, r.id DESC
                """, (entity, target_system, target_table))
                rows = cur.fetchall()
        return [_review_view(dict(row)) for row in rows]
    finally:
        if owns:
            central.close()


def _suspect_reference_reviews(entity: str, target_system: str, ref: dict,
                               central: PostgresCentralConnector | None = None,
                               ) -> list[dict]:
    table = ref.get("target_table")
    missing_column = str(ref.get("target_column") or "")
    if not table:
        return []
    candidates = _mapping_reviews_for_table(
        entity, target_system, str(table), central=central)
    suspect_columns = {"id", missing_column}
    if missing_column.endswith("_id"):
        suspect_columns.add(missing_column.removesuffix("_id"))
    suspects = []
    for row in candidates:
        source_column = str(row.get("source_column") or "")
        target_column = str(row.get("target_column") or "")
        if target_column not in suspect_columns and not target_column.endswith("_id"):
            continue
        if source_column == target_column or source_column == "id":
            continue
        suspects.append(row)
    return suspects


def _review_view(row: dict) -> dict:
    return {
        "review_id": row.get("review_id") or row.get("id"),
        "proposal_id": row.get("proposal_id"),
        "source_column": row.get("source_column"),
        "target_table": row.get("suggested_target_table") or row.get("target_table"),
        "target_column": (
            row.get("resolved_target_column")
            or row.get("suggested_target_column")
            or row.get("target_column")
        ),
    }


def _crosswalk_conflicts(central: PostgresCentralConnector, target_system: str,
                         target_table: str, target_id: str, entity: str,
                         external_reference: str | None) -> list[dict]:
    with central.connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT source_system, source_entity, external_reference,
                       target_system, target_table, target_id
                FROM integration.id_crosswalk
                WHERE target_system = %s AND target_table = %s AND target_id = %s
            """, (target_system, target_table, target_id))
            rows = [dict(r) for r in cur.fetchall()]
    return [
        r for r in rows
        if not (r["source_entity"] == entity
                and (external_reference is None
                     or str(r["external_reference"]) == external_reference))
    ]


def _target_row_exists(table: str, column: str, value: str, target=None) -> bool:
    target = target or configured_target()
    try:
        return target.fetch_row_by(table, column, value) is not None
    except Exception:
        return False
