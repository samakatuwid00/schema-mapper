"""IRIMSV Region V -> LRMIS staging delivery worker."""
from __future__ import annotations

import argparse
import json
import os
import threading
from datetime import datetime, timezone

from src.connectors import MySQLStagingConnector, PostgresCentralConnector
from src.integration_store import (
    approved_mapping, claim_events, delivered, quarantine, retry_or_dead_letter,
    save_projection,
)
from src.schema_models import Schema
from src.transform_engine import transform_row

TARGET_SYSTEM = os.environ.get("LRMIS_TARGET_SYSTEM", "LRMIS")
DEFAULT_INTERVAL = int(os.environ.get("SYNC_INTERVAL_SECONDS", "300"))
DEFAULT_BATCH_SIZE = int(os.environ.get("SYNC_BATCH_SIZE", "100"))
MAX_ATTEMPTS = int(os.environ.get("SYNC_MAX_ATTEMPTS", "8"))


def _target_schema(conn, fingerprint: str) -> Schema | None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT schema_document FROM integration.schema_version
            WHERE target_system = %s AND fingerprint = %s AND approved_at IS NOT NULL
        """, (TARGET_SYSTEM, fingerprint))
        row = cur.fetchone()
        return Schema.from_dict(row[0]) if row else None


def _outbound_row(event: dict, mapping: dict, transformed: dict) -> dict:
    active = event["operation"] != "deactivate"
    transformed.setdefault("active", active)
    return {
        "event_id": str(event["event_id"]),
        "external_reference": str(event["external_reference"]),
        "source_system": event["source_system"],
        "operation": event["operation"],
        "source_updated_at": event["source_updated_at"].astimezone(timezone.utc).replace(tzinfo=None),
        "mapping_version": mapping["version"],
        "payload_checksum": event["payload_checksum"],
        **transformed,
    }


def process_once(central: PostgresCentralConnector | None = None,
                 staging: MySQLStagingConnector | None = None,
                 batch_size: int = DEFAULT_BATCH_SIZE) -> dict:
    owns_central = central is None
    central = central or PostgresCentralConnector()
    staging = staging or MySQLStagingConnector()
    result = {"claimed": 0, "delivered": 0, "quarantined": 0, "retried": 0}
    try:
        with central.connection() as conn:
            events = claim_events(conn, TARGET_SYSTEM, batch_size)
            result["claimed"] = len(events)
            for event in events:
                mapping = approved_mapping(conn, event["source_entity"], event["target_system"])
                if not mapping:
                    quarantine(conn, event, ["no approved mapping for entity"], None)
                    result["quarantined"] += 1
                    continue

                # Handle mappings as JSON string or list
                mappings = mapping["mappings"]
                if isinstance(mappings, str):
                    mappings = json.loads(mappings)

                target_schema = _target_schema(conn, mapping["schema_fingerprint"])
                if not target_schema:
                    quarantine(conn, event, ["mapping schema version is not approved"], mapping["id"])
                    result["quarantined"] += 1
                    continue
                transformed, errors = transform_row(event["payload"],
                    {"mappings": mappings}, target_schema)
                if errors:
                    quarantine(conn, event, errors, mapping["id"])
                    result["quarantined"] += 1
                    continue
                outbound = _outbound_row(event, mapping, transformed)
                save_projection(conn, event, mapping, outbound)
                try:
                    staging.upsert(mapping["target_table"], outbound)
                    delivered(conn, event, mapping)
                    result["delivered"] += 1
                except Exception as exc:
                    retry_or_dead_letter(conn, event, exc, MAX_ATTEMPTS)
                    result["retried"] += 1
            conn.commit()
        return result
    finally:
        if owns_central:
            central.close()


def run_loop(stop_event: threading.Event,
             interval: int = DEFAULT_INTERVAL,
             batch_size: int = DEFAULT_BATCH_SIZE,
             on_result=None) -> None:
    """Run delivery passes until stop_event is set; at most one in-flight batch after stop."""
    central = PostgresCentralConnector()
    staging = MySQLStagingConnector()
    try:
        while not stop_event.is_set():
            started = datetime.now(timezone.utc).isoformat()
            try:
                result = {"started_at": started,
                          **process_once(central, staging, batch_size)}
            except Exception as exc:
                result = {"started_at": started, "worker_error": str(exc)}
            if on_result:
                on_result(result)
            stop_event.wait(interval)
    finally:
        central.close()


def main():
    parser = argparse.ArgumentParser(description="Deliver approved IRIMSV events to LRMIS staging")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args()
    if not args.loop:
        started = datetime.now(timezone.utc).isoformat()
        try:
            print(json.dumps({"started_at": started, **process_once(batch_size=args.batch_size)}, default=str))
        except Exception as exc:
            print(json.dumps({"started_at": started, "worker_error": str(exc)}))
        return
    run_loop(threading.Event(), args.interval, args.batch_size,
             on_result=lambda r: print(json.dumps(r, default=str)))


if __name__ == "__main__":
    main()
