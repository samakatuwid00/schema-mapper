"""
End-to-end demo of the pipeline using fake data, so you can see the
whole flow before wiring up real databases.

Run: python example/run_demo.py
(Set GEMINI_API_KEY to use the real AI mapping engine; otherwise
this falls back to a hardcoded mock proposal so the demo still runs.)
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.schema_models import Schema, Table, Column
from src.schema_ingest import parse_ddl
from src.mapping_engine import propose_mapping, FieldMapping
from src.mapping_store import split_by_confidence, apply_auto_matches, push_to_review_queue
from src.transform_engine import transform_batch

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "active_mapping_config.json")
QUEUE_PATH = os.path.join(os.path.dirname(__file__), "review_queue.json")


def our_central_schema() -> Schema:
    """This is YOUR schema -- stable, never changes per target system."""
    return Schema(system_name="our_central_db", tables=[
        Table(name="customer", columns=[
            Column(name="id", data_type="integer", nullable=False, is_primary_key=True),
            Column(name="full_name", data_type="string", nullable=False,
                   description="Customer's display name"),
            Column(name="email", data_type="string", nullable=False),
            Column(name="phone", data_type="string", nullable=True),
            Column(name="created_at", data_type="datetime", nullable=True),
            Column(name="status", data_type="string", nullable=True,
                   description="active, inactive, or churned"),
        ])
    ])


def load_their_schema() -> Schema:
    with open(os.path.join(os.path.dirname(__file__), "their_schema.ddl.sql")) as f:
        return parse_ddl(f.read(), system_name="their_system")


def get_mapping(source_table, target_schema):
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        print("Calling Google Gemini for live mapping proposals...")
        return propose_mapping(source_table, target_schema)

    print("No GEMINI_API_KEY set -- using a mock proposal so the demo still runs.")
    return [
        FieldMapping("id", "customers", "cust_id", 0.95, "none", "Both are the primary key"),
        FieldMapping("full_name", "customers", "cust_nm", 0.92, "none", "Both hold the customer name"),
        FieldMapping("email", "customers", "email_addr", 0.97, "none", "Clear semantic match"),
        FieldMapping("phone", "customers", "phone_no", 0.9, "none", "Clear semantic match"),
        FieldMapping("created_at", "customers", "signup_dt", 0.8, "cast:date->datetime",
                     "Likely match but naming is ambiguous -- flagged for review"),
        FieldMapping("status", "customers", "acct_status", 0.6, "enum_remap:{\"churned\": \"cancelled\"}",
                     "Enum values may differ between systems -- needs human confirmation"),
    ]


def main():
    central = our_central_schema()
    their_schema = load_their_schema()
    customer_table = central.get_table("customer")

    mappings = get_mapping(customer_table, their_schema)

    auto, needs_review = split_by_confidence(mappings)
    print(f"\n{len(auto)} auto-applied, {len(needs_review)} sent to review queue:\n")
    for m in needs_review:
        print(f"  [REVIEW] {m.source_column} -> {m.target_table}.{m.target_column} "
              f"(confidence {m.confidence}): {m.reasoning}")

    config = apply_auto_matches(CONFIG_PATH, customer_table.name, auto)
    push_to_review_queue(QUEUE_PATH, customer_table.name, needs_review)

    print(f"\nActive mapping config (v{config['version']}) saved to {CONFIG_PATH}")
    print(f"Review queue saved to {QUEUE_PATH}\n")

    # Simulate a few rows coming out of the central DB's outbox
    sample_rows = [
        {"id": 1, "full_name": "Ada Lovelace", "email": "ada@example.com",
         "phone": "555-0100", "created_at": None, "status": "active"},
        {"id": 2, "full_name": "", "email": "grace@example.com",  # will fail validation
         "phone": None, "created_at": None, "status": "active"},
    ]

    good, failed = transform_batch(sample_rows, config, their_schema)

    print("Transformed rows ready for staging DB:")
    for row in good:
        print(f"  {row}")

    print("\nFailed rows (sent to dead-letter queue, NOT written to staging):")
    for f in failed:
        print(f"  {f['source_row']} -> {f['errors']}")


if __name__ == "__main__":
    main()
