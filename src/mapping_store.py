"""
Owns the "active mapping config" -- the JSON file the transform engine
actually reads at run time. Never edited by hand in production; only
written by the mapping engine (auto-applied matches) or by a human
via the review queue (everything below the confidence threshold).
"""
import json
import os
import time
from .mapping_engine import FieldMapping

AUTO_APPLY_THRESHOLD = 0.85


def split_by_confidence(mappings: list[FieldMapping], threshold: float = AUTO_APPLY_THRESHOLD):
    auto = [m for m in mappings if m.confidence >= threshold and m.target_table]
    needs_review = [m for m in mappings if m.confidence < threshold or not m.target_table]
    return auto, needs_review


def load_active_config(path: str) -> dict:
    if not os.path.exists(path):
        return {"version": 0, "mappings": [], "source_table": None}
    with open(path) as f:
        return json.load(f)


def save_active_config(path: str, config: dict):
    config["updated_at"] = time.time()
    with open(path, "w") as f:
        json.dump(config, f, indent=2)


def apply_auto_matches(config_path: str, source_table: str, auto_matches: list[FieldMapping]):
    """
    Merges newly auto-approved matches into the active config, bumping
    the version. Existing entries for the same source_column are
    replaced (last mapping wins), so re-running mapping after a schema
    change naturally supersedes stale entries.
    """
    config = load_active_config(config_path)
    config["source_table"] = source_table
    existing = {m["source_column"]: m for m in config.get("mappings", [])}
    for m in auto_matches:
        existing[m.source_column] = {
            "source_column": m.source_column,
            "target_table": m.target_table,
            "target_column": m.target_column,
            "confidence": m.confidence,
            "transform": m.transform,
            "approved_by": "auto",
        }
    config["mappings"] = list(existing.values())
    config["version"] = config.get("version", 0) + 1
    save_active_config(config_path, config)
    return config


def push_to_review_queue(queue_path: str, source_table: str, needs_review: list[FieldMapping]):
    """
    Appends low-confidence / unmatched columns to a human review queue.
    A human (or a small review UI) later calls `resolve_review_item`
    to promote an item into the active config.
    """
    queue = []
    if os.path.exists(queue_path):
        with open(queue_path) as f:
            queue = json.load(f)
    for m in needs_review:
        queue.append({
            "source_table": source_table,
            "source_column": m.source_column,
            "suggested_target_table": m.target_table,
            "suggested_target_column": m.target_column,
            "confidence": m.confidence,
            "reasoning": m.reasoning,
            "status": "pending",
        })
    with open(queue_path, "w") as f:
        json.dump(queue, f, indent=2)
    return queue


def resolve_review_item(config_path: str, queue_path: str, source_column: str,
                         target_table: str, target_column: str, transform: str = "none"):
    """A human confirms/corrects a pending item; promotes it into the active config."""
    config = load_active_config(config_path)
    existing = {m["source_column"]: m for m in config.get("mappings", [])}
    existing[source_column] = {
        "source_column": source_column,
        "target_table": target_table,
        "target_column": target_column,
        "confidence": 1.0,
        "transform": transform,
        "approved_by": "human",
    }
    config["mappings"] = list(existing.values())
    config["version"] = config.get("version", 0) + 1
    save_active_config(config_path, config)

    if os.path.exists(queue_path):
        with open(queue_path) as f:
            queue = json.load(f)
        queue = [q for q in queue if not (q["source_column"] == source_column and q["status"] == "pending")]
        with open(queue_path, "w") as f:
            json.dump(queue, f, indent=2)

    return config
