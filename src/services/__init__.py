"""Reusable service layer wrapping pipeline/admin workflow logic.

The admin API (src/admin_api) and the CLI entry points both call these
functions so web and terminal paths cannot drift. Services return plain
dicts and raise typed exceptions from .common instead of printing or
calling sys.exit.
"""
from .common import ConflictError, NotFoundError, ServiceError, ValidationError

__all__ = [
    "ServiceError",
    "NotFoundError",
    "ConflictError",
    "ValidationError",
]
