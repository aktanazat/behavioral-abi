"""SQLite v0 registry schema -- the exact four tables from
IMPLEMENTATION-PLAN.md's "Registry v0" section, no more, no fewer columns.

`claims` deliberately has no declared primary key: the plan lists `PK`
next to `subjects.digest`, `observations.contract_digest`, and
`aliases.alias`, but not next to any `claims` column, which reads as a
plain fact table rather than an accident. `Registry.insert_contract`
(store.py) is what keeps it duplicate-free in practice, by skipping claim
inserts for a `contract_digest` it has already recorded (content-addressed
observations are write-once, so a repeat insert is always the exact same
claims).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS subjects (
    digest TEXT PRIMARY KEY,
    registry_name TEXT,
    registry_version TEXT,
    package_or_endpoint TEXT NOT NULL,
    protocol_revision TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    contract_digest TEXT PRIMARY KEY,
    subject_digest TEXT NOT NULL REFERENCES subjects(digest),
    observed_at TEXT NOT NULL,
    predicate_type TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    rekor_log_index INTEGER,
    supersedes_digest TEXT
);
CREATE INDEX IF NOT EXISTS observations_subject_idx ON observations(subject_digest, observed_at);

CREATE TABLE IF NOT EXISTS claims (
    contract_digest TEXT NOT NULL REFERENCES observations(contract_digest),
    property TEXT NOT NULL,
    status TEXT NOT NULL,
    value_json TEXT,
    coverage_json TEXT
);
CREATE INDEX IF NOT EXISTS claims_contract_idx ON claims(contract_digest);
CREATE INDEX IF NOT EXISTS claims_property_idx ON claims(contract_digest, property);

CREATE TABLE IF NOT EXISTS aliases (
    alias TEXT PRIMARY KEY,
    subject_digest TEXT NOT NULL REFERENCES subjects(digest),
    valid_from TEXT NOT NULL,
    valid_until TEXT
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the registry database at `path` and
    ensure its schema exists. `path` may be `":memory:"` for tests.

    `check_same_thread=False`: `Registry` is designed to be shared by a
    `ThreadingHTTPServer`, which handles each request on its own thread.
    Safe only because `Registry` itself serializes every access with a
    lock (see `babi.registry.store`) -- never share this raw connection
    across threads without that lock.
    """
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


__all__ = ["SCHEMA_SQL", "connect"]
