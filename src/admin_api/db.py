"""Process-wide pooled connectors shared by all request handlers and jobs."""
from __future__ import annotations

from ..connectors import MySQLStagingConnector, PostgresCentralConnector

_central: PostgresCentralConnector | None = None
_staging: MySQLStagingConnector | None = None
_target: MySQLStagingConnector | None = None


def central() -> PostgresCentralConnector:
    global _central
    if _central is None:
        _central = PostgresCentralConnector()
    return _central


def staging() -> MySQLStagingConnector:
    global _staging
    if _staging is None:
        _staging = MySQLStagingConnector()
    return _staging


def target() -> MySQLStagingConnector:
    """Path B connector to lrmis_target (its own pool, same credentials)."""
    global _target
    if _target is None:
        _target = MySQLStagingConnector.for_target()
    return _target


def close_all() -> None:
    global _central
    if _central is not None:
        _central.close()
        _central = None
