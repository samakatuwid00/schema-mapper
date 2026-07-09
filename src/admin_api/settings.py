"""Environment-driven configuration for the admin API."""
from __future__ import annotations

import os

ADMIN_API_HOST = os.environ.get("ADMIN_API_HOST", "127.0.0.1")
ADMIN_API_PORT = int(os.environ.get("ADMIN_API_PORT", "8400"))
ADMIN_SESSION_SECRET = os.environ.get("ADMIN_SESSION_SECRET", "")
SESSION_COOKIE = "schema_mapper_session"
SESSION_MAX_AGE_SECONDS = int(os.environ.get("ADMIN_SESSION_MAX_AGE", str(12 * 3600)))

JOB_HEARTBEAT_SECONDS = 10
JOB_STALE_SECONDS = 60
JOB_WORKERS = int(os.environ.get("ADMIN_JOB_WORKERS", "4"))


def require_session_secret() -> str:
    if not ADMIN_SESSION_SECRET:
        raise RuntimeError(
            "ADMIN_SESSION_SECRET is not set - add it to .env before starting the admin API")
    return ADMIN_SESSION_SECRET
