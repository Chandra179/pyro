"""Shared FastAPI dependencies.

`api/data.py` used to sit here as a passthrough module: seven functions, each one building a fresh
`Settings`, opening a database, and delegating a single call. It carried no logic of its own, and
because each route called several of them, one `/data/panel` request — polled every four seconds —
re-read `.env` and `config.yaml` three times and re-ran the ArangoDB schema bootstrap three times.

FastAPI's dependency system is the framework feature that replaces it: one connection resolved
per request, injected into the routes that need it, and overridable in tests via
`app.dependency_overrides` instead of monkeypatching module globals.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends

from pyro.config import Settings, get_settings
from pyro.db import Database, open_db_from_settings


def get_db() -> Iterator[Database]:
    """Request-scoped handle onto the process-wide ArangoDB connection (see db/connection.py —
    the underlying client and schema bootstrap are cached, so this is cheap per request)."""
    with open_db_from_settings(get_settings()) as db:
        yield db


SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[Database, Depends(get_db)]
