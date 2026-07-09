"""Minimal administrator/auditor CLI; suitable for wrapping in a future web UI."""
from __future__ import annotations

import argparse
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import psycopg2.extras

from src.connectors import PostgresCentralConnector
from src.integration_store import replay
from src.mapping_repository import approve


def status(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT status, count(*) AS events, min(created_at) AS oldest
            FROM integration.outbox GROUP BY status ORDER BY status
        """)
        queues = [dict(r) for r in cur.fetchall()]
        cur.execute("""
            SELECT source_entity, target_system, enabled, paused_reason, updated_at
            FROM integration.entity_control ORDER BY source_entity
        """)
        entities = [dict(r) for r in cur.fetchall()]
    print(json.dumps({"queues": queues, "entities": entities}, default=str, indent=2))


def set_enabled(conn, entity: str, target: str, enabled: bool, reason: str | None):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO integration.entity_control (source_entity, target_system, enabled, paused_reason)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source_entity, target_system)
            DO UPDATE SET enabled = EXCLUDED.enabled, paused_reason = EXCLUDED.paused_reason, updated_at = now()
        """, (entity, target, enabled, reason))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    replay_parser = sub.add_parser("replay")
    replay_parser.add_argument("event_id")
    approve_parser = sub.add_parser("approve-mapping")
    approve_parser.add_argument("mapping_id", type=int)
    approve_parser.add_argument("--by", required=True)
    schema_parser = sub.add_parser("approve-schema")
    schema_parser.add_argument("fingerprint")
    schema_parser.add_argument("--target", default="LRMIS")
    schema_parser.add_argument("--by", required=True)
    control = sub.add_parser("entity")
    control.add_argument("name")
    control.add_argument("--target", default="LRMIS")
    control.add_argument("--enable", action="store_true")
    control.add_argument("--disable", action="store_true")
    control.add_argument("--reason")
    args = parser.parse_args()
    connector = PostgresCentralConnector()
    try:
        with connector.connection() as conn:
            if args.command == "status":
                status(conn)
            elif args.command == "replay":
                replay(conn, args.event_id)
            elif args.command == "approve-mapping":
                approve(conn, args.mapping_id, args.by)
            elif args.command == "approve-schema":
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE integration.schema_version SET approved_at = now(), approved_by = %s
                        WHERE target_system = %s AND fingerprint = %s
                    """, (args.by, args.target, args.fingerprint))
                    if cur.rowcount != 1:
                        raise ValueError("schema fingerprint not found")
            elif args.command == "entity":
                if args.enable == args.disable:
                    parser.error("choose exactly one of --enable or --disable")
                set_enabled(conn, args.name, args.target, args.enable,
                            None if args.enable else (args.reason or "manually paused"))
            conn.commit()
    finally:
        connector.close()


if __name__ == "__main__":
    main()
