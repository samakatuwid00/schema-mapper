"""CDC strategies (§6.2-6.5).

* `TriggerCDC`  — reuse the existing Postgres trigger → `integration.outbox`
  (the current default); events are already captured, so `pending_events` reads
  undelivered outbox rows.
* `PollingCDC`  — any engine with a monotonic timestamp column; polls
  `WHERE <ts> > ?`. No per-table setup.
* `DebeziumCDC` — placeholder for a Kafka/Debezium topic consumer.
"""
from __future__ import annotations

import psycopg2.extras

from ..connectors import safe_identifier


class TriggerCDC:
    name = "trigger"

    def __init__(self, outbox_table: str = "integration.outbox"):
        self.outbox_table = outbox_table

    def setup(self, source_conn, table: str) -> None:
        return None            # trigger is installed by the SQL migrations

    def teardown(self, source_conn, table: str) -> None:
        return None

    def pending_events(self, central_conn, table: str, after) -> list[dict]:
        with central_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT * FROM {self.outbox_table}
                WHERE source_table = %s AND created_at > %s
                ORDER BY created_at
            """, (table, after))
            return [dict(r) for r in cur.fetchall()]


class PollingCDC:
    name = "polling"

    def __init__(self, schema: str = "irimsv", timestamp_column: str = "updated_at"):
        self.schema = schema
        self.timestamp_column = timestamp_column

    def setup(self, source_conn, table: str) -> None:
        return None

    def teardown(self, source_conn, table: str) -> None:
        return None

    def build_sql(self, table: str) -> str:
        safe_identifier(table)
        safe_identifier(self.schema)
        safe_identifier(self.timestamp_column)
        ts = self.timestamp_column
        return (f'SELECT * FROM "{self.schema}"."{table}" '
                f'WHERE "{ts}" > %s ORDER BY "{ts}"')

    def pending_events(self, source_conn, table: str, after) -> list[dict]:
        with source_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(self.build_sql(table), (after,))
            return [dict(r) for r in cur.fetchall()]


class DebeziumCDC:
    name = "debezium"

    def __init__(self, topic: str | None = None, consumer=None):
        self.topic = topic
        self._consumer = consumer          # inject a Kafka consumer

    def setup(self, source_conn, table: str) -> None:
        return None

    def teardown(self, source_conn, table: str) -> None:
        return None

    def pending_events(self, source_conn, table: str, after) -> list[dict]:
        if self._consumer is None:
            raise NotImplementedError(
                "DebeziumCDC needs a Kafka consumer injected; wire one to poll the topic")
        return list(self._consumer.poll(self.topic, after))


_STRATEGIES = {"trigger": TriggerCDC, "polling": PollingCDC, "debezium": DebeziumCDC}


def get_cdc_strategy(config: dict | None = None):
    """Resolve a strategy from engine config, e.g.
    `{"strategy": "polling", "options": {"timestamp_column": "modified_at"}}`."""
    config = config or {"strategy": "trigger"}
    name = (config.get("strategy") or "trigger").strip().lower()
    cls = _STRATEGIES.get(name)
    if cls is None:
        raise ValueError(f"unknown CDC strategy: {name!r}")
    return cls(**(config.get("options") or {}))
