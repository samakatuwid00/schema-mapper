"""Backward-compatible central connection helper.

New integration code uses pooled adapters from ``src.connectors``. This helper remains
only for small IRIMSV-side scripts; direct target database helpers were intentionally
removed so application code cannot bypass the audited delivery worker.
"""
import os

import psycopg2

CENTRAL_DB_URL = os.environ.get(
    "CENTRAL_DB_URL", "postgresql://postgres:postgres@localhost:5433/central"
)


def central_conn():
    return psycopg2.connect(CENTRAL_DB_URL)
