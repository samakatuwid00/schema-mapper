"""Typed exceptions shared by all services; the API maps them to HTTP codes."""
from __future__ import annotations


class ServiceError(Exception):
    """Base class for service-level failures."""


class NotFoundError(ServiceError):
    """Requested entity/proposal/event does not exist."""


class ConflictError(ServiceError):
    """A concurrent operation holds the resource (maps to HTTP 409)."""


class ValidationError(ServiceError):
    """Input or state precondition failed (maps to HTTP 422)."""
