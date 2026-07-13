"""Pluggable change-data-capture (generic engine, §6).

Different source engines expose changes differently. A `CDCStrategy` abstracts
"what changed since T" so the delivery loop is agnostic: the existing Postgres
trigger→outbox path is `TriggerCDC`, any engine with an `updated_at` column can
use `PollingCDC`, and Kafka/Debezium is `DebeziumCDC`. Selectable via config.
"""
from __future__ import annotations

from ._protocols import CDCStrategy
from .strategies import TriggerCDC, PollingCDC, DebeziumCDC, get_cdc_strategy

__all__ = [
    "CDCStrategy",
    "TriggerCDC",
    "PollingCDC",
    "DebeziumCDC",
    "get_cdc_strategy",
]
