"""Schema comparison and selective pause policy."""
from __future__ import annotations

from dataclasses import asdict

import psycopg2.extras

from .schema_models import Schema


def diff_schemas(previous: Schema, observed: Schema) -> list[dict]:
    differences = []
    previous_tables = {t.name: t for t in previous.tables}
    observed_tables = {t.name: t for t in observed.tables}
    for table_name in sorted(previous_tables.keys() | observed_tables.keys()):
        before = previous_tables.get(table_name)
        after = observed_tables.get(table_name)
        if before is None:
            differences.append({"kind": "table_added", "table": table_name, "breaking": False})
            continue
        if after is None:
            differences.append({"kind": "table_removed", "table": table_name, "breaking": True})
            continue
        old_cols = {c.name: c for c in before.columns}
        new_cols = {c.name: c for c in after.columns}
        for column_name in sorted(old_cols.keys() | new_cols.keys()):
            old = old_cols.get(column_name)
            new = new_cols.get(column_name)
            if old is None:
                breaking = not new.nullable
                differences.append({"kind": "column_added", "table": table_name,
                                    "column": column_name, "breaking": breaking,
                                    "after": asdict(new)})
            elif new is None:
                differences.append({"kind": "column_removed", "table": table_name,
                                    "column": column_name, "breaking": True,
                                    "before": asdict(old)})
            else:
                changes = {}
                for attribute in ("data_type", "nullable", "is_primary_key"):
                    if getattr(old, attribute) != getattr(new, attribute):
                        changes[attribute] = {"before": getattr(old, attribute),
                                              "after": getattr(new, attribute)}
                if changes:
                    breaking = (
                        "data_type" in changes or "is_primary_key" in changes or
                        ("nullable" in changes and not new.nullable)
                    )
                    differences.append({"kind": "column_changed", "table": table_name,
                                        "column": column_name, "breaking": breaking,
                                        "changes": changes})
    return differences


def impacted_entities(conn, target_system: str, differences: list[dict]) -> list[str]:
    changed_tables = {d["table"] for d in differences if d.get("breaking")}
    if not changed_tables:
        return []
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT source_entity FROM integration.mapping_version
            WHERE target_system = %s AND status = 'approved' AND target_table = ANY(%s)
        """, (target_system, list(changed_tables)))
        return [row[0] for row in cur.fetchall()]


def record_drift(conn, target_system: str, previous_fingerprint: str | None,
                 observed_fingerprint: str, differences: list[dict],
                 drift_pair: str = "source->staging") -> list[str]:
    impacted = impacted_entities(conn, target_system, differences)
    breaking = any(d.get("breaking") for d in differences)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO integration.schema_drift_report
                (target_system, previous_fingerprint, observed_fingerprint,
                 differences, impacted_entities, breaking, drift_pair)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (target_system, previous_fingerprint, observed_fingerprint,
              psycopg2.extras.Json(differences), impacted, breaking, drift_pair))
        if impacted:
            cur.execute("""
                UPDATE integration.entity_control SET enabled = false,
                    paused_reason = %s, updated_at = now()
                WHERE target_system = %s AND source_entity = ANY(%s)
            """, (f"Breaking LRMIS schema drift {observed_fingerprint[:12]}",
                  target_system, impacted))
            cur.execute("""
                UPDATE integration.mapping_version SET status = 'paused'
                WHERE target_system = %s AND source_entity = ANY(%s) AND status = 'approved'
            """, (target_system, impacted))
    return impacted
