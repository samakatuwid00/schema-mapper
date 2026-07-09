"""Process-wide pooled connectors shared by all request handlers and jobs."""
from __future__ import annotations

from ..connectors import MySQLStagingConnector, PostgresCentralConnector

_central: PostgresCentralConnector | None = None
_staging: MySQLStagingConnector | None = None


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


def close_all() -> None:
    global _central
    if _central is not None:
        _central.close()
        _central = None
