"""IRIMSV Region V -> LRMIS direct delivery worker.

Single delivery path: every approved event is fanned directly into the real
target via `deliver_event`/`GenericWriter`. The legacy single-table staging
route was removed in retire-legacy-staging §1."""
from __future__ import annotations

import argparse
import json
import os
import threading
from datetime import datetime, timezone

from src.connectors import MySQLStagingConnector, PostgresCentralConnector
from src.integration_store import (
    claim_events, mark_event_delivered, quarantine, retry_or_dead_letter,
)
from src.lrmis_delivery import deliver_event, load_entity_mappings

TARGET_SYSTEM = os.environ.get("LRMIS_TARGET_SYSTEM", "LRMIS")
DEFAULT_INTERVAL = int(os.environ.get("SYNC_INTERVAL_SECONDS", "300"))
DEFAULT_BATCH_SIZE = int(os.environ.get("SYNC_BATCH_SIZE", "100"))
MAX_ATTEMPTS = int(os.environ.get("SYNC_MAX_ATTEMPTS", "8"))


def _open_target(holder: dict) -> None:
    """Open the Path B target connection + writer, engine-selected by config.

    Default (and unchanged) is the MySQL target with the legacy writer. Set
    `LRMIS_TARGET_ENGINE=postgres` to stream into a Postgres target through the
    dialect-aware `GenericWriter` (the target's schema is discovered from the
    adapter, so the whole pipeline agrees on it)."""
    engine = os.environ.get("LRMIS_TARGET_ENGINE", "mysql").strip().lower()
    if engine in ("postgres", "postgresql", "pg"):
        from src.adapters import get_target_adapter
        from src.adapters.lrmis_plugin import resolve_plugin
        from src.delivery import GenericWriter
        adapter = get_target_adapter("postgres")
        holder["cm"] = adapter.connection()
        holder["conn"] = holder["cm"].__enter__()
        holder["writer"] = GenericWriter(adapter.dialect(), adapter.discover_registry(),
                                         plugin=resolve_plugin())
    else:
        connector = MySQLStagingConnector.for_target()
        holder["cm"] = connector.connection()
        holder["conn"] = holder["cm"].__enter__()
        holder["writer"] = None            # legacy MySQL writer (deliver_event default)


def _deliver_path_b(conn, target, event, result, agent=None) -> None:
    """Multi-table delivery into lrmis_target for an onboarded entity.

    The target writes for one event are committed as a unit and rolled back on
    failure, so a partial fan-out never persists; the crosswalk rows the writer
    records live on the central connection and commit with the batch."""
    entity_name = event["source_entity"]
    mappings = load_entity_mappings(conn, entity_name, event["target_system"])
    if not mappings:
        quarantine(conn, event, ["no approved LRMIS-target mapping for entity"], None)
        result["quarantined"] += 1
        return
    try:
        outcome = deliver_event(target_conn_of(target), conn, entity_name=entity_name,
                                event=event, mappings=mappings,
                                target_system=event["target_system"],
                                writer=target.get("writer"))
    except Exception as exc:                       # transport / DB failure
        _rollback(target)
        retry_or_dead_letter(conn, event, exc, MAX_ATTEMPTS)
        result["retried"] += 1
        return
    if outcome["status"] != "delivered":
        _rollback(target)
        reasons = list(outcome.get("errors", ["delivery error"]))
        if agent is not None:                          # §8.6: heal triggers on delivery error
            proposal = agent.heal("; ".join(str(r) for r in reasons),
                                  {"entity": entity_name})
            reasons.append(f"agent_heal={proposal.action}:{proposal.detail}")
        quarantine(conn, event, reasons, None)
        result["quarantined"] += 1
        return
    _commit(target)
    mark_event_delivered(conn, event)
    result["delivered"] += 1


def target_conn_of(holder: dict):
    return holder["conn"]


def _commit(holder: dict) -> None:
    holder["conn"].commit()


def _rollback(holder: dict) -> None:
    holder["conn"].rollback()


def process_once(central: PostgresCentralConnector | None = None,
                 batch_size: int = DEFAULT_BATCH_SIZE, agent=None) -> dict:
    owns_central = central is None
    central = central or PostgresCentralConnector()
    result = {"claimed": 0, "delivered": 0, "quarantined": 0, "retried": 0, "lrmis": 0}
    # Single delivery path: every event fans directly into the real target. The
    # target connection is opened lazily on the first event of a non-empty batch.
    target_holder: dict = {}
    try:
        with central.connection() as conn:
            events = claim_events(conn, TARGET_SYSTEM, batch_size)
            result["claimed"] = len(events)
            for event in events:
                if "conn" not in target_holder:
                    _open_target(target_holder)
                _deliver_path_b(conn, target_holder, event, result, agent)
                result["lrmis"] += 1
            conn.commit()
        return result
    finally:
        if "cm" in target_holder:
            target_holder["cm"].__exit__(None, None, None)
        if owns_central:
            central.close()


def run_loop(stop_event: threading.Event,
             interval: int = DEFAULT_INTERVAL,
             batch_size: int = DEFAULT_BATCH_SIZE,
             on_result=None) -> None:
    """Run delivery passes until stop_event is set; at most one in-flight batch after stop."""
    central = PostgresCentralConnector()
    try:
        while not stop_event.is_set():
            started = datetime.now(timezone.utc).isoformat()
            try:
                result = {"started_at": started,
                          **process_once(central, batch_size)}
            except Exception as exc:
                result = {"started_at": started, "worker_error": str(exc)}
            if on_result:
                on_result(result)
            stop_event.wait(interval)
    finally:
        central.close()


def main():
    parser = argparse.ArgumentParser(description="Deliver approved IRIMSV events to the LRMIS target")
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
