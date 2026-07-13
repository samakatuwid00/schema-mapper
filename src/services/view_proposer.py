"""Propose flattening views for source tables with unmapped columns.

When the source schema is more normalized than the target, some target
columns may not exist in the matching source table.  This module detects
those gaps, searches other source tables for the missing columns, and
generates a CREATE VIEW with the necessary JOINs so the view can be
onboarded as a single "table".
"""
from __future__ import annotations

import json
import logging
import os
import re

import psycopg2.extras

from ..connectors import PostgresCentralConnector
from .common import NotFoundError, ValidationError

log = logging.getLogger(__name__)

VIEW_PROPOSAL_TABLE = "integration.view_proposal"
PROJECTION_SCHEMA = os.environ.get("LRMIS_PROJECTION_SCHEMA", "lrmis_projection")
VIEW_PROPOSAL_DDL = """
    CREATE TABLE IF NOT EXISTS integration.view_proposal (
        id BIGSERIAL PRIMARY KEY,
        entity_id BIGINT NOT NULL REFERENCES integration.onboarding_entity(id),
        source_schema TEXT NOT NULL,
        source_table TEXT NOT NULL,
        target_system TEXT NOT NULL,
        view_schema TEXT NOT NULL DEFAULT 'lrmis_projection',
        view_name TEXT NOT NULL,
        view_sql TEXT NOT NULL,
        joined_tables JSONB NOT NULL DEFAULT '[]',
        mapped_columns JSONB NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'applied', 'rejected')),
        pending_proposal_id BIGINT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        applied_at TIMESTAMPTZ,
        applied_by TEXT
    )
"""


def _ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute(VIEW_PROPOSAL_DDL)
        cur.execute("ALTER TABLE integration.view_proposal "
                    "ADD COLUMN IF NOT EXISTS view_schema TEXT NOT NULL "
                    "DEFAULT 'lrmis_projection'")
        cur.execute("ALTER TABLE integration.view_proposal "
                    "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()")
    conn.commit()


def _query(conn, sql, params=None):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params or ())
        return [dict(r) for r in cur.fetchall()]


def _fetchval(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        r = cur.fetchone()
        return r[0] if r else None


# ---------------------------------------------------------------------------
# Foreign-key discovery  (PostgreSQL information_schema)
# ---------------------------------------------------------------------------

def _discover_fks(conn, schema: str) -> list[dict]:
    """Return FK relationships for the given schema.

    Each row: {table_name, column_name, foreign_table, foreign_column}
    """
    sql = """
        SELECT
            tc.table_name,
            kcu.column_name,
            ccu.table_name AS foreign_table,
            ccu.column_name AS foreign_column
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
            ON ccu.constraint_name = tc.constraint_name
            AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
            AND tc.table_schema = %s
    """
    return _query(conn, sql, (schema,))


def _primary_keys(conn, schema: str) -> dict[str, str]:
    """table -> primary_key_column_name for every table in schema."""
    sql = """
        SELECT kcu.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
            AND tc.table_schema = %s
    """
    rows = _query(conn, sql, (schema,))
    return {r["table_name"]: r["column_name"] for r in rows}


def _all_tables(conn, schema: str) -> list[str]:
    sql = """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = %s AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """
    return [r["table_name"] for r in _query(conn, sql, (schema,))]


def _all_columns(conn, schema: str) -> dict[str, list[str]]:
    """table -> [column_name, ...] for every table in schema."""
    sql = """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = %s
        ORDER BY ordinal_position
    """
    rows = _query(conn, sql, (schema,))
    grouped: dict[str, list[str]] = {}
    for r in rows:
        grouped.setdefault(r["table_name"], []).append(r["column_name"])
    return grouped


# ---------------------------------------------------------------------------
# Join-path detection
# ---------------------------------------------------------------------------

def _find_join_path(
    source_table: str,
    target_column: str,
    tables: list[str],
    columns: dict[str, list[str]],
    pks: dict[str, str],
    fks: list[dict],
    target_table: str | None = None,
) -> list[dict]:
    """Find a join path from source_table to a table that has target_column.

    Returns a list of join edges:
      [{from_table, from_col, to_table, to_col}, ...]
    or empty list if no path found.
    """
    # Build FK index: (table -> [(column, foreign_table, foreign_column)])
    fk_from: dict[str, list[tuple[str, str, str]]] = {}
    for fk in fks:
        fk_from.setdefault(fk["table_name"], []).append(
            (fk["column_name"], fk["foreign_table"], fk["foreign_column"])
        )

    # Build reverse FK index: (foreign_table -> [(column, table, foreign_column)])
    fk_to: dict[str, list[tuple[str, str, str]]] = {}
    for fk in fks:
        fk_to.setdefault(fk["foreign_table"], []).append(
            (fk["foreign_column"], fk["table_name"], fk["column_name"])
        )

    # BFS from source_table to find a table that has target_column
    visited = {source_table}
    queue = [(source_table, [])]

    while queue:
        current, path = queue.pop(0)

        # If current table has the target column, we're done
        reached_target = current == target_table if target_table else current != source_table
        if reached_target and target_column in columns.get(current, []):
            return path

        # Follow outgoing FKs
        for col, ft, fcol in fk_from.get(current, []):
            if ft not in visited:
                visited.add(ft)
                queue.append((
                    ft,
                    path + [{"from_table": current, "from_col": col,
                             "to_table": ft, "to_col": fcol}],
                ))

        # Follow incoming FKs (reverse direction)
        for col, ft, fcol in fk_to.get(current, []):
            if ft not in visited:
                visited.add(ft)
                queue.append((
                    ft,
                    path + [{"from_table": current, "from_col": col,
                             "to_table": ft, "to_col": fcol}],
                ))

    # No FK path found — try naming heuristics
    # Look for a table named <something> where target_column lives,
    # and source_table has a column <something>_id or <singular_of_tablename>_id
    for tbl in tables:
        if tbl == source_table:
            continue
        if target_table and tbl != target_table:
            continue
        if target_column in columns.get(tbl, []):
            # Check if source_table has a FK-like column to this table
            singular = re.sub(r"es$|s$", "", tbl)  # crude singularisation
            candidates = [f"{tbl}_id", f"{singular}_id"]
            pk = pks.get(tbl, "id")
            for candidate in candidates:
                if candidate in columns.get(source_table, []):
                    return [{"from_table": source_table, "from_col": candidate,
                             "to_table": tbl, "to_col": pk}]

    return []  # no path found


def _unique_projection_aliases(columns: list[dict]) -> list[dict]:
    """Give every projected column a deterministic, unique output name."""
    used: set[str] = set()
    result: list[dict] = []
    for item in columns:
        updated = dict(item)
        base = str(updated.get("alias") or updated["column"])
        alias = base
        if alias in used:
            alias = f"{updated['table']}_{base}"
        suffix = 2
        candidate = alias
        while candidate in used:
            candidate = f"{alias}_{suffix}"
            suffix += 1
        updated["alias"] = candidate
        used.add(candidate)
        result.append(updated)
    return result


# ---------------------------------------------------------------------------
# View SQL generation
# ---------------------------------------------------------------------------

def _generate_view_sql(
    source_schema: str,
    source_table: str,
    view_schema: str,
    view_name: str,
    base_columns: list[dict],
    joins: list[dict],
) -> str:
    """Generate CREATE OR REPLACE VIEW SQL.

    *base_columns* describes what to SELECT:
      [{"table": "schools", "column": "id", "alias": "id"},
       {"table": "schools", "column": "name", "alias": "school_name"},
       {"table": "districts", "column": "name", "alias": "district_name"}, ...]

    *joins* is the join path edges from _find_join_path.
    """
    def qi(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    table_aliases = {source_table: "s"}
    next_alias = 1
    for j in joins:
        for table in (j["from_table"], j["to_table"]):
            if table not in table_aliases:
                table_aliases[table] = f"t{next_alias}"
                next_alias += 1

    # SELECT clause
    select_parts = []
    for bc in base_columns:
        alias = table_aliases.get(bc["table"], bc["table"])
        col = bc["column"]
        col_alias = bc.get("alias", col)
        if col_alias == col:
            select_parts.append(f'    {qi(alias)}.{qi(col)}')
        else:
            select_parts.append(f'    {qi(alias)}.{qi(col)} AS {qi(col_alias)}')

    # FROM clause
    from_clause = f'    {qi(source_schema)}.{qi(source_table)} {qi("s")}'

    # JOIN clauses
    join_clauses = []
    joined = {source_table}
    for j in joins:
        if j["to_table"] in joined:
            continue
        if j["from_table"] not in joined:
            raise ValidationError(
                f"invalid join order: {j['from_table']} must be joined before {j['to_table']}"
            )
        from_alias = table_aliases.get(j["from_table"], j["from_table"])
        to_alias = table_aliases.get(j["to_table"], j["to_table"])
        join_clauses.append(
            f'    LEFT JOIN {qi(source_schema)}.{qi(j["to_table"])} {qi(to_alias)}'
            f' ON {qi(to_alias)}.{qi(j["to_col"])} = {qi(from_alias)}.{qi(j["from_col"])}'
        )
        joined.add(j["to_table"])

    parts = [
        f'CREATE OR REPLACE VIEW {qi(view_schema)}.{qi(view_name)} AS',
        'SELECT',
        ',\n'.join(select_parts),
        f'FROM {from_clause}',
    ]
    if join_clauses:
        parts.extend(join_clauses)

    return '\n'.join(parts) + ';\n'


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def propose_view(
    entity_id: int,
    source_schema: str,
    source_table: str,
    target_system: str,
    central: PostgresCentralConnector | None = None,
) -> dict:
    """Analyze a pending proposal and generate a flattening view proposal.

    1. Fetches the entity's most recent proposal
    2. Identifies target columns that have no source match
    3. Searches other source tables for those columns
    4. Generates a CREATE VIEW with appropriate JOINs
    5. Stores the view proposal in integration.view_proposal

    Returns the view proposal dict.
    """
    owns = central is None
    central = central or PostgresCentralConnector()

    try:
        with central.connection() as conn:
            _ensure_table(conn)

            # 1. Get the entity + its most recent proposal
            entity = _fetchval(conn, """
                SELECT row_to_json(e)::text FROM integration.onboarding_entity e
                WHERE id = %s AND source_schema = %s AND source_table = %s
                  AND target_system = %s
            """, (entity_id, source_schema, source_table, target_system))
            if not entity:
                raise NotFoundError(
                    f"entity id={entity_id} {source_schema}.{source_table} not found")
            entity = json.loads(entity)

            proposals = _query(conn, """
                SELECT * FROM integration.onboarding_proposal
                WHERE entity_id = %s ORDER BY created_at DESC LIMIT 1
            """, (entity_id,))
            if not proposals:
                raise ValidationError(
                    f"no proposal found for entity id={entity_id}")

            proposal = proposals[0]

            # 2. Identify unmapped target columns from the proposal
            mappings = proposal.get("mappings", []) or []
            mapped_target_cols = {
                m["target_column"]
                for m in mappings
                if m.get("target_column") and m.get("target_table")
            }
            unmet = proposal.get("unmet_required_columns", []) or []

            # Also get rejected mappings - they might hint at needed joins
            rejected_source_cols = {}
            for m in mappings:
                if (not m.get("target_column") or not m.get("target_table")
                        or m.get("confidence", 0) == 0):
                    rejected_source_cols[m["source_column"]] = m

            # 3. Discover source schema
            tables = _all_tables(conn, source_schema)
            columns = _all_columns(conn, source_schema)
            pks = _primary_keys(conn, source_schema)
            fks = _discover_fks(conn, source_schema)

            # 4. For each unmet required column, find which source table has it
            join_edges: list[dict] = []
            mapped_cols: list[dict] = []

            # Start with all columns from the source table
            source_cols_in_target = set()
            for m in mappings:
                if m.get("target_column") and m.get("target_table"):
                    source_cols_in_target.add(m["target_column"])

            # Add base columns from the source table
            for col_name in columns.get(source_table, []):
                mapped_cols.append({
                    "table": source_table,
                    "column": col_name,
                    "alias": col_name,
                })

            # For each unmet required column, try to find it in another source table
            for target_col in unmet:
                # Remove table prefix if present (e.g., "schools.name" -> "name")
                plain_col = target_col.split(".")[-1] if "." in target_col else target_col

                # Skip envelope fields
                if plain_col in ("event_id", "external_reference", "source_system",
                                 "operation", "source_updated_at", "mapping_version",
                                 "payload_checksum", "active", "accepted_at"):
                    continue

                # Search other source tables for this column
                found = False
                for tbl in tables:
                    if tbl == source_table:
                        continue
                    if plain_col in columns.get(tbl, []):
                        # Find a join path
                        path = _find_join_path(
                            source_table, plain_col, tables, columns, pks, fks,
                            target_table=tbl,
                        )
                        if path:
                            # Add join edges
                            for edge in path:
                                if edge not in join_edges:
                                    join_edges.append(edge)

                            mapped_cols.append({
                                "table": tbl,
                                "column": plain_col,
                                "alias": plain_col,
                            })
                            found = True
                            break

                if not found:
                    log.info("Could not find %s in any source table", plain_col)

            # Also try to map rejected source columns to their target tables
            for src_col, mapping in rejected_source_cols.items():
                # If the AI suggested a target_table but no target_column,
                # it might be a cross-table candidate
                target_table = mapping.get("target_table")
                target_col = mapping.get("target_column")
                if target_table and not target_col:
                    # Search for this source column in the target
                    for tbl in tables:
                        if tbl == source_table:
                            continue
                        if src_col in columns.get(tbl, []):
                            path = _find_join_path(
                                source_table, src_col, tables, columns, pks, fks,
                                target_table=tbl,
                            )
                            if path:
                                for edge in path:
                                    if edge not in join_edges:
                                        join_edges.append(edge)
                                mapped_cols.append({
                                    "table": tbl,
                                    "column": src_col,
                                    "alias": src_col,
                                })
                                break

            # 5. Generate the view name
            view_name = f"{source_table}_for_lrmis"
            view_schema = PROJECTION_SCHEMA

            # Remove duplicate base source columns
            seen = set()
            deduped_mapped_cols = []
            for mc in mapped_cols:
                key = (mc["table"], mc["column"])
                if key not in seen:
                    seen.add(key)
                    deduped_mapped_cols.append(mc)
            deduped_mapped_cols = _unique_projection_aliases(deduped_mapped_cols)

            # 6. Skip if no joins needed — view would just duplicate the original table
            if not join_edges:
                return {
                    "proposal_id": None,
                    "entity_id": entity_id,
                    "source_table": source_table,
                    "view_schema": view_schema,
                    "view_name": None,
                    "view_sql": None,
                    "joined_tables": [],
                    "mapped_columns": deduped_mapped_cols,
                    "status": "skipped",
                    "message": "No cross-table joins needed — onboard the original table directly",
                }

            # 7. Generate view SQL
            view_sql = _generate_view_sql(
                source_schema, source_table, view_schema, view_name,
                deduped_mapped_cols, join_edges,
            )

            # 8. Store the view proposal
            existing = _query(conn, """
                SELECT id FROM integration.view_proposal
                WHERE entity_id = %s AND status = 'pending'
            """, (entity_id,))
            if existing:
                # Update existing
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE integration.view_proposal
                        SET view_sql = %s, joined_tables = %s,
                            mapped_columns = %s, view_schema = %s, updated_at = now()
                        WHERE id = %s
                    """, (view_sql, json.dumps(join_edges),
                          json.dumps(deduped_mapped_cols), view_schema, existing[0]["id"]))
                conn.commit()
                proposal_id = existing[0]["id"]
            else:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO integration.view_proposal
                            (entity_id, source_schema, source_table, target_system,
                             view_name, view_sql, joined_tables, mapped_columns,
                             pending_proposal_id, status, view_schema)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
                        RETURNING id
                    """, (entity_id, source_schema, source_table, target_system,
                          view_name, view_sql, json.dumps(join_edges),
                          json.dumps(deduped_mapped_cols), proposal["id"], view_schema))
                    proposal_id = cur.fetchone()[0]
                conn.commit()

            return {
                "proposal_id": proposal_id,
                "entity_id": entity_id,
                "source_table": source_table,
                "view_schema": view_schema,
                "view_name": view_name,
                "view_sql": view_sql,
                "joined_tables": join_edges,
                "mapped_columns": deduped_mapped_cols,
                "status": "pending",
            }

    finally:
        if owns:
            central.close()


def list_view_proposals(
    status: str | None = None,
    central: PostgresCentralConnector | None = None,
) -> list[dict]:
    """List all view proposals, optionally filtered by status."""
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            _ensure_table(conn)
            if status:
                rows = _query(conn, """
                    SELECT * FROM integration.view_proposal
                    WHERE status = %s ORDER BY created_at DESC
                """, (status,))
            else:
                rows = _query(conn, """
                    SELECT * FROM integration.view_proposal
                    ORDER BY created_at DESC
                """)
            return rows
    finally:
        if owns:
            central.close()


def apply_view(
    proposal_id: int,
    actor: str,
    central: PostgresCentralConnector | None = None,
) -> dict:
    """Execute the view SQL and mark the proposal as applied."""
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        # Read first, then release the transaction before regenerating. The
        # proposal refresh performs its own DDL guard and must not wait behind
        # a lock held by this same request.
        with central.connection() as conn:
            _ensure_table(conn)
            proposals = _query(conn, """
                SELECT * FROM integration.view_proposal WHERE id = %s
            """, (proposal_id,))
            if not proposals:
                raise NotFoundError(f"view proposal id={proposal_id} not found")
            proposal = proposals[0]
            if proposal["status"] == "applied":
                raise ValidationError("view already applied")

        # Pending proposals may have been generated by an older path finder.
        # Rebuild them against the current schema before executing the SQL.
        refreshed = propose_view(
            proposal["entity_id"], proposal["source_schema"],
            proposal["source_table"], proposal["target_system"], central=central,
        )
        if refreshed.get("status") == "skipped":
            raise ValidationError(refreshed.get("message", "view no longer needed"))

        with central.connection() as conn:
            proposals = _query(conn, """
                SELECT * FROM integration.view_proposal WHERE id = %s
            """, (proposal_id,))
            proposal = proposals[0]
            view_sql = proposal["view_sql"]
            with conn.cursor() as cur:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {PROJECTION_SCHEMA}")
                cur.execute(view_sql)
                cur.execute("""
                    UPDATE integration.view_proposal
                    SET status = 'applied', applied_at = now(), applied_by = %s
                    WHERE id = %s
                """, (actor, proposal_id))
                cur.execute("""
                    INSERT INTO integration.onboarding_entity
                        (source_schema, source_table, target_system, status)
                    VALUES (%s, %s, %s, 'discovered')
                    ON CONFLICT (source_system, source_schema, source_table, target_system)
                    DO UPDATE SET updated_at = now()
                    RETURNING id
                """, (proposal.get("view_schema") or PROJECTION_SCHEMA,
                      proposal["view_name"], proposal["target_system"]))
                projection_entity_id = cur.fetchone()[0]
            conn.commit()

            return {
                "proposal_id": proposal_id,
                "entity_id": projection_entity_id,
                "view_schema": proposal.get("view_schema") or PROJECTION_SCHEMA,
                "view_name": proposal["view_name"],
                "status": "applied",
                "view_sql": view_sql,
            }
    finally:
        if owns:
            central.close()
