"""Registry SQLite storage: exact schema (db.py), resolve/insert/latest/
history round trips, append-only supersession, write-once re-insertion,
and alias bookkeeping."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from babi.evidence import EvidenceStore
from babi.registry.db import connect
from babi.registry.store import Registry, RegistryError

from _fixtures import OTHER_TOOL_SUBJECT, TOOL_SUBJECT, make_record


def _registry(tmp_path: Path) -> Registry:
    return Registry(tmp_path / "registry.sqlite3", EvidenceStore(tmp_path / "evidence"))


# -- exact schema ---------------------------------------------------------------


def test_schema_has_exactly_the_plan_columns(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite3")
    expected = {
        "subjects": {
            "digest",
            "registry_name",
            "registry_version",
            "package_or_endpoint",
            "protocol_revision",
            "first_seen_at",
            "last_seen_at",
        },
        "observations": {
            "contract_digest",
            "subject_digest",
            "observed_at",
            "predicate_type",
            "evidence_digest",
            "rekor_log_index",
            "supersedes_digest",
        },
        "claims": {"contract_digest", "property", "status", "value_json", "coverage_json"},
        "aliases": {"alias", "subject_digest", "valid_from", "valid_until"},
    }
    for table, columns in expected.items():
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        assert {row[1] for row in rows} == columns, table
    conn.close()


def test_connect_is_idempotent_and_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "registry.sqlite3"
    conn_a = connect(path)
    conn_a.close()
    conn_b = connect(path)  # must not fail re-running CREATE TABLE IF NOT EXISTS
    conn_b.close()
    assert path.is_file()


def test_claims_table_has_no_declared_primary_key(tmp_path: Path) -> None:
    """The plan lists `PK` on subjects/observations/aliases but not on any
    claims column -- deliberately a plain fact table."""
    conn = connect(tmp_path / "db.sqlite3")
    rows = conn.execute("PRAGMA table_info(claims)").fetchall()
    assert all(row[5] == 0 for row in rows)  # column 5 = pk flag
    conn.close()


# -- resolve() --------------------------------------------------------------------


def test_resolve_computes_exact_digest_and_is_deterministic(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    tool_definition = {"name": "create_widget", "inputSchema": {"type": "object"}}
    digest_1 = registry.resolve(
        registry_name="widgets-mcp", registry_version="1.0.0", package_or_endpoint="@example/widgets-mcp",
        runtime_artifact_digest=None, protocol_revision="2026-07-28", tool_definition=tool_definition,
    )
    digest_2 = registry.resolve(
        registry_name="widgets-mcp", registry_version="1.0.0", package_or_endpoint="@example/widgets-mcp",
        runtime_artifact_digest=None, protocol_revision="2026-07-28", tool_definition=tool_definition,
    )
    assert digest_1 == digest_2
    assert digest_1.startswith("sha256:")
    registry.close()


def test_resolve_never_overwrites_known_registry_name_with_null(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    tool_definition = {"name": "create_widget", "inputSchema": {"type": "object"}}
    digest = registry.resolve(
        registry_name="widgets-mcp", registry_version="1.0.0", package_or_endpoint="@example/widgets-mcp",
        runtime_artifact_digest=None, protocol_revision="2026-07-28", tool_definition=tool_definition,
    )
    registry.resolve(
        registry_name=None, registry_version=None, package_or_endpoint="@example/widgets-mcp",
        runtime_artifact_digest=None, protocol_revision="2026-07-28", tool_definition=tool_definition,
    )
    row = registry._conn.execute("SELECT registry_name, registry_version FROM subjects WHERE digest = ?", (digest,)).fetchone()
    assert row["registry_name"] == "widgets-mcp"
    assert row["registry_version"] == "1.0.0"
    registry.close()


# -- insert_contract / round trip --------------------------------------------------


def test_insert_contract_round_trips_through_latest_history_and_cas(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    record = make_record()
    contract_digest = registry.insert_contract(record)

    latest = registry.latest(TOOL_SUBJECT)
    assert latest is not None
    assert latest.contract_digest == contract_digest
    assert latest.subject_digest == TOOL_SUBJECT
    assert latest.predicate_type == "https://behavioral-abi.dev/contract/v0"
    assert latest.evidence_digest == record["statement"]["predicate"]["evidence_ref"]["sha256"]
    assert latest.rekor_log_index == 424242
    assert latest.supersedes_digest is None

    history = registry.history(TOOL_SUBJECT)
    assert [o.contract_digest for o in history] == [contract_digest]

    assert registry.get_contract(contract_digest) == record
    assert json.loads(registry.get_evidence(contract_digest)) == record
    registry.close()


def test_insert_contract_records_claims(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    contract_digest = registry.insert_contract(make_record(idempotent_status="observed_false"))
    claims = registry.claims_for(contract_digest)
    assert len(claims) == 1
    assert claims[0].property == "idempotent"
    assert claims[0].status == "observed_false"
    assert claims[0].value == {"runs": 2, "detail": None}
    assert claims[0].coverage == {"scope": "REPEAT", "evidence_digests": []}
    registry.close()


def test_insert_contract_creates_subject_row_without_explicit_resolve(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.insert_contract(make_record())
    row = registry._conn.execute("SELECT * FROM subjects WHERE digest = ?", (TOOL_SUBJECT,)).fetchone()
    assert row is not None
    assert row["package_or_endpoint"] == "@example/widgets-mcp"
    assert row["protocol_revision"] == "2026-07-28"
    assert row["registry_name"] is None  # not derivable from a contract alone -- honest, not fabricated
    registry.close()


def test_insert_contract_re_insertion_is_a_write_once_noop(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    record = make_record()
    digest_1 = registry.insert_contract(record)
    digest_2 = registry.insert_contract(record)
    assert digest_1 == digest_2
    assert len(registry.history(TOOL_SUBJECT)) == 1
    assert len(registry.claims_for(digest_1)) == 1  # not duplicated
    registry.close()


def test_insert_contract_rejects_malformed_records(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    with pytest.raises(RegistryError):
        registry.insert_contract({"statement": "not-an-object"})
    with pytest.raises(RegistryError):
        registry.insert_contract({"statement": {"predicate": {}, "subject": []}})
    with pytest.raises(RegistryError):
        record = make_record()
        del record["statement"]["predicate"]["verified_at"]
        registry.insert_contract(record)
    registry.close()


# -- append-only supersession ------------------------------------------------------


def test_supersession_is_append_only_never_mutates_the_prior_row(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    old_record = make_record(verified_at="2026-08-20T00:00:00Z", idempotent_status="observed_true")
    old_digest = registry.insert_contract(old_record)

    new_record = make_record(
        verified_at="2026-08-21T00:00:00Z", idempotent_status="observed_false", supersedes=old_digest
    )
    new_digest = registry.insert_contract(new_record)
    assert new_digest != old_digest

    history = registry.history(TOOL_SUBJECT)
    assert [o.contract_digest for o in history] == [old_digest, new_digest]
    # the OLD row is untouched: still points nowhere, still says observed_true
    assert history[0].supersedes_digest is None
    assert registry.claims_for(old_digest)[0].status == "observed_true"
    # the NEW row carries the supersedes link and the new status
    assert history[1].supersedes_digest == old_digest
    assert registry.claims_for(new_digest)[0].status == "observed_false"

    # latest() returns the newer observation
    latest = registry.latest(TOOL_SUBJECT)
    assert latest.contract_digest == new_digest
    registry.close()


def test_history_is_scoped_to_one_subject(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    d1 = registry.insert_contract(make_record(tool_subject=TOOL_SUBJECT))
    d2 = registry.insert_contract(make_record(tool_subject=OTHER_TOOL_SUBJECT, tool_name="other_tool"))
    assert [o.contract_digest for o in registry.history(TOOL_SUBJECT)] == [d1]
    assert [o.contract_digest for o in registry.history(OTHER_TOOL_SUBJECT)] == [d2]
    registry.close()


def test_latest_and_history_return_empty_for_unknown_subject(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    unknown = "sha256:" + "00" * 32
    assert registry.latest(unknown) is None
    assert registry.history(unknown) == []
    registry.close()


# -- aliases ------------------------------------------------------------------------


def test_insert_contract_sets_an_alias_to_the_subject(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.insert_contract(make_record())
    assert registry.resolve_alias("mcp:widgets/create_widget") == TOOL_SUBJECT
    registry.close()


def test_set_alias_rebinding_overwrites_and_retire_clears(tmp_path: Path) -> None:
    # aliases.subject_digest carries a foreign key onto subjects: a real alias
    # can only ever point at a subject the registry already knows about.
    registry = _registry(tmp_path)
    registry.insert_contract(make_record(tool_subject=TOOL_SUBJECT))
    registry.insert_contract(make_record(tool_subject=OTHER_TOOL_SUBJECT, tool_name="other_tool"))

    registry.set_alias("my-alias", TOOL_SUBJECT)
    assert registry.resolve_alias("my-alias") == TOOL_SUBJECT
    registry.set_alias("my-alias", OTHER_TOOL_SUBJECT)
    assert registry.resolve_alias("my-alias") == OTHER_TOOL_SUBJECT
    registry.retire_alias("my-alias")
    assert registry.resolve_alias("my-alias") is None
    registry.close()


def test_set_alias_unknown_subject_raises_foreign_key_error(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        registry.set_alias("dangling-alias", "sha256:" + "99" * 32)
    registry.close()


def test_resolve_alias_unknown_returns_none(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    assert registry.resolve_alias("does-not-exist") is None
    registry.close()


# -- digest normalization -----------------------------------------------------------


def test_digest_lookups_accept_bare_hex_or_prefixed_form(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.insert_contract(make_record())
    bare = TOOL_SUBJECT.removeprefix("sha256:")
    assert registry.latest(bare) is not None
    assert registry.latest(bare).subject_digest == TOOL_SUBJECT
    registry.close()


def test_malformed_digest_raises_registry_error(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    with pytest.raises(RegistryError):
        registry.latest("not-a-digest")
    with pytest.raises(RegistryError):
        registry.history("also-not-a-digest")
    registry.close()


def test_registry_context_manager_closes(tmp_path: Path) -> None:
    with _registry(tmp_path) as registry:
        registry.insert_contract(make_record())
    with pytest.raises(sqlite3.ProgrammingError):
        registry._conn.execute("SELECT 1")
