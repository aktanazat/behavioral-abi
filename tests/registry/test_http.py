"""Minimal stdlib HTTP API: every route in IMPLEMENTATION-PLAN.md's
"Registry v0" API surface, plus strict-parsing/bounded-body error paths."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from babi.evidence import EvidenceStore
from babi.registry.http import MAX_BODY_BYTES, make_server
from babi.registry.store import Registry

from _fixtures import TOOL_SUBJECT, make_record


@pytest.fixture
def server_url(tmp_path: Path) -> Iterator[tuple[str, Registry]]:
    registry = Registry(tmp_path / "registry.sqlite3", EvidenceStore(tmp_path / "evidence"))
    server = make_server(registry, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", registry
    finally:
        server.shutdown()
        thread.join(timeout=5)
        registry.close()


def _get(base: str, path: str) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(base + path, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _get_raw(base: str, path: str) -> tuple[int, bytes, str | None]:
    try:
        with urllib.request.urlopen(base + path, timeout=5) as response:
            return response.status, response.read(), response.headers.get("Content-Type")
    except urllib.error.HTTPError as error:
        return error.code, error.read(), None


def _post(base: str, path: str, body: bytes, *, content_type: str = "application/json") -> tuple[int, object]:
    request = urllib.request.Request(base + path, data=body, method="POST", headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


# -- GET /v0/subjects/{digest}/latest --------------------------------------------


def test_latest_returns_the_stored_observation(server_url: tuple[str, Registry]) -> None:
    base, registry = server_url
    contract_digest = registry.insert_contract(make_record())
    status, body = _get(base, f"/v0/subjects/{TOOL_SUBJECT}/latest")
    assert status == 200
    assert body["contract_digest"] == contract_digest
    assert body["subject_digest"] == TOOL_SUBJECT


def test_latest_404_for_unknown_subject(server_url: tuple[str, Registry]) -> None:
    base, _registry = server_url
    status, body = _get(base, f"/v0/subjects/sha256:{'00' * 32}/latest")
    assert status == 404
    assert "error" in body


def test_latest_400_for_malformed_digest(server_url: tuple[str, Registry]) -> None:
    base, _registry = server_url
    status, body = _get(base, "/v0/subjects/not-a-digest/latest")
    assert status == 400
    assert "error" in body


# -- GET /v0/subjects/{digest}/history -------------------------------------------


def test_history_returns_all_observations_in_order(server_url: tuple[str, Registry]) -> None:
    base, registry = server_url
    d1 = registry.insert_contract(make_record(verified_at="2026-08-19T00:00:00Z"))
    d2 = registry.insert_contract(
        make_record(verified_at="2026-08-20T00:00:00Z", idempotent_status="observed_false", supersedes=d1)
    )
    status, body = _get(base, f"/v0/subjects/{TOOL_SUBJECT}/history")
    assert status == 200
    assert [o["contract_digest"] for o in body["observations"]] == [d1, d2]


def test_history_empty_list_for_unknown_subject(server_url: tuple[str, Registry]) -> None:
    base, _registry = server_url
    status, body = _get(base, f"/v0/subjects/sha256:{'00' * 32}/history")
    assert status == 200
    assert body == {"observations": []}


# -- GET /v0/subjects/{digest}/policy --------------------------------------------


def test_policy_default_intent_is_retry(server_url: tuple[str, Registry]) -> None:
    base, registry = server_url
    registry.insert_contract(make_record(idempotent_status="observed_false"))
    status, body = _get(base, f"/v0/subjects/{TOOL_SUBJECT}/policy")
    assert status == 200
    assert body["decision"] == "deny_retry"


def test_policy_explicit_intent_query_param(server_url: tuple[str, Registry]) -> None:
    base, registry = server_url
    registry.insert_contract(make_record(idempotent_status="observed_true"))
    status, body = _get(base, f"/v0/subjects/{TOOL_SUBJECT}/policy?intent=retry")
    assert status == 200
    assert body["decision"] == "allow"


def test_policy_unknown_subject_returns_unknown_not_404(server_url: tuple[str, Registry]) -> None:
    """A missing/unresolvable contract is a 200 with decision:"unknown",
    never a 404 or an "allow" -- registries answer policy questions with a
    decision, not an HTTP miss."""
    base, _registry = server_url
    status, body = _get(base, f"/v0/subjects/sha256:{'00' * 32}/policy")
    assert status == 200
    assert body["decision"] == "unknown"


# -- GET /v0/contracts/{digest} ---------------------------------------------------


def test_contracts_returns_the_full_record(server_url: tuple[str, Registry]) -> None:
    base, registry = server_url
    record = make_record()
    contract_digest = registry.insert_contract(record)
    status, body = _get(base, f"/v0/contracts/{contract_digest}")
    assert status == 200
    assert body == record


def test_contracts_404_for_unknown_digest(server_url: tuple[str, Registry]) -> None:
    base, _registry = server_url
    status, _body = _get(base, f"/v0/contracts/sha256:{'00' * 32}")
    assert status == 404


# -- GET /v0/evidence/{digest} ----------------------------------------------------


def test_evidence_returns_raw_bytes(server_url: tuple[str, Registry]) -> None:
    base, registry = server_url
    evidence_digest = registry._evidence.put_json({"kind": "probe-evidence/v0", "n": 1})
    status, raw, content_type = _get_raw(base, f"/v0/evidence/{evidence_digest}")
    assert status == 200
    assert json.loads(raw) == {"kind": "probe-evidence/v0", "n": 1}
    assert content_type == "application/octet-stream"


def test_evidence_404_for_unknown_digest(server_url: tuple[str, Registry]) -> None:
    base, _registry = server_url
    status, _raw, _ct = _get_raw(base, f"/v0/evidence/sha256:{'00' * 32}")
    assert status == 404


# -- POST /v0/resolve ---------------------------------------------------------------


def test_resolve_returns_the_exact_subject_digest(server_url: tuple[str, Registry]) -> None:
    base, _registry = server_url
    payload = {
        "tool_definition": {"name": "create_widget", "inputSchema": {"type": "object"}},
        "package_or_endpoint": "@example/widgets-mcp",
        "protocol_revision": "2026-07-28",
        "registry_name": "widgets-mcp",
        "registry_version": "1.0.0",
    }
    status, body = _post(base, "/v0/resolve", json.dumps(payload).encode())
    assert status == 200
    assert body["subject_digest"].startswith("sha256:")

    # resolving the same definition twice is deterministic
    status2, body2 = _post(base, "/v0/resolve", json.dumps(payload).encode())
    assert body2["subject_digest"] == body["subject_digest"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"tool_definition": {"name": "x"}},  # missing package_or_endpoint/protocol_revision
        {"tool_definition": "not-an-object", "package_or_endpoint": "p", "protocol_revision": "r"},
        {"tool_definition": {}, "package_or_endpoint": "", "protocol_revision": "r"},
        {"tool_definition": {}, "package_or_endpoint": "p", "protocol_revision": "r", "registry_name": 5},
    ],
)
def test_resolve_400_for_malformed_body(server_url: tuple[str, Registry], payload: dict) -> None:
    base, _registry = server_url
    status, body = _post(base, "/v0/resolve", json.dumps(payload).encode())
    assert status == 400
    assert "error" in body


def test_resolve_400_for_non_json_body(server_url: tuple[str, Registry]) -> None:
    base, _registry = server_url
    status, body = _post(base, "/v0/resolve", b"not json at all")
    assert status == 400


def test_resolve_413_for_oversized_body(server_url: tuple[str, Registry]) -> None:
    base, _registry = server_url
    oversized = json.dumps({"tool_definition": {"pad": "x" * (MAX_BODY_BYTES + 1024)}}).encode()
    status, body = _post(base, "/v0/resolve", oversized)
    assert status == 413


# -- routing ------------------------------------------------------------------------


def test_unknown_route_is_404(server_url: tuple[str, Registry]) -> None:
    base, _registry = server_url
    status, _body = _get(base, "/v0/nonexistent")
    assert status == 404


def test_get_on_resolve_path_is_404(server_url: tuple[str, Registry]) -> None:
    base, _registry = server_url
    status, _body = _get(base, "/v0/resolve")
    assert status == 404


def test_post_on_get_only_path_is_404(server_url: tuple[str, Registry]) -> None:
    base, _registry = server_url
    status, _body = _post(base, f"/v0/subjects/{TOOL_SUBJECT}/latest", b"{}")
    assert status == 404
