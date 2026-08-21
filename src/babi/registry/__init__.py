"""Registry v0: SQLite index + content-addressed store over attested
behavioral contracts, exact-digest resolve/latest/history/policy lookups,
and a minimal stdlib HTTP API."""

from __future__ import annotations

from babi.registry.db import SCHEMA_SQL, connect
from babi.registry.http import make_handler, make_server
from babi.registry.policy import DEFAULT_INTENT, DEFAULT_MAX_AGE, PolicyResult, decide
from babi.registry.store import ClaimRow, Observation, Registry, RegistryError

__all__ = [
    "SCHEMA_SQL",
    "ClaimRow",
    "DEFAULT_INTENT",
    "DEFAULT_MAX_AGE",
    "Observation",
    "PolicyResult",
    "Registry",
    "RegistryError",
    "connect",
    "decide",
    "make_handler",
    "make_server",
]
