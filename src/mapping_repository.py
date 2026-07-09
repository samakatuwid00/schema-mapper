"""Immutable, reviewed database-backed mapping versions."""
from __future__ import annotations

import psycopg2.extras


def propose(conn, source_entity: str, target_system: str, target_table: str,
            schema_fingerprint: str, mappings: list[dict]) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COALESCE(MAX(version), 0) + 1 FROM integration.mapping_version
            WHERE source_entity = %s AND target_system = %s
        """, (source_entity, target_system))
        version = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO integration.mapping_version
                (source_entity, target_system, target_table, schema_fingerprint,
                 version, mappings, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'draft') RETURNING id
        """, (source_entity, target_system, target_table, schema_fingerprint,
              version, psycopg2.extras.Json(mappings)))
        return cur.fetchone()[0]


def approve(conn, mapping_id: int, approved_by: str):
    with conn.cursor() as cur:
        cur.execute("SELECT source_entity, target_system FROM integration.mapping_version WHERE id = %s FOR UPDATE",
                    (mapping_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("mapping version not found")
        source_entity, target_system = row
        cur.execute("""
            UPDATE integration.mapping_version SET status = 'superseded'
            WHERE source_entity = %s AND target_system = %s AND status = 'approved'
        """, (source_entity, target_system))
        cur.execute("""
            UPDATE integration.mapping_version SET status = 'approved', approved_at = now(), approved_by = %s
            WHERE id = %s AND status IN ('draft', 'paused')
        """, (approved_by, mapping_id))
        if cur.rowcount != 1:
            raise ValueError("only draft or paused mappings can be approved")
        cur.execute("""
            INSERT INTO integration.entity_control (source_entity, target_system, enabled)
            VALUES (%s, %s, true)
            ON CONFLICT (source_entity, target_system)
            DO UPDATE SET enabled = true, paused_reason = NULL, updated_at = now()
        """, (source_entity, target_system))
