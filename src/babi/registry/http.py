"""Minimal stdlib HTTP API over the registry -- no framework, matching
IMPLEMENTATION-PLAN.md's "Registry v0" API surface exactly:

    GET  /v0/subjects/{digest}/latest
    GET  /v0/subjects/{digest}/history
    GET  /v0/subjects/{digest}/policy?intent=retry
    GET  /v0/contracts/{digest}
    GET  /v0/evidence/{digest}
    POST /v0/resolve

Every path digest segment and POST body field is strictly parsed and
bounds-checked before it reaches the registry; malformed input gets a 4xx,
never an unhandled exception reaching the request thread.
"""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from babi.canon import Json
from babi.registry.store import Registry, RegistryError

MAX_BODY_BYTES = 1 << 20  # 1 MiB -- contract/evidence bodies are fetched by digest, never POSTed whole here
MAX_INTENT_LENGTH = 64

_DIGEST_RE = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
_SUBJECT_LATEST_RE = re.compile(r"^/v0/subjects/([^/]+)/latest$")
_SUBJECT_HISTORY_RE = re.compile(r"^/v0/subjects/([^/]+)/history$")
_SUBJECT_POLICY_RE = re.compile(r"^/v0/subjects/([^/]+)/policy$")
_CONTRACT_RE = re.compile(r"^/v0/contracts/([^/]+)$")
_EVIDENCE_RE = re.compile(r"^/v0/evidence/([^/]+)$")
_RESOLVE_PATH = "/v0/resolve"

_STRING_OR_NULL_FIELDS = ("registry_name", "registry_version", "runtime_artifact_digest")


def make_handler(registry: Registry) -> type[BaseHTTPRequestHandler]:
    """Build a `BaseHTTPRequestHandler` subclass bound to `registry`.
    A factory (rather than a module-level class) because stdlib's
    `HTTPServer` wants a class, not an instance, and every request needs
    the same `registry`."""

    class RegistryRequestHandler(BaseHTTPRequestHandler):
        server_version = "babi-registry/0.1"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
            pass  # quiet by default; callers that want logs can override this

        def _send_json(self, status: HTTPStatus, payload: Json) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: HTTPStatus, message: str) -> None:
            self._send_json(status, {"error": message})

        def _digest_or_none(self, raw: str) -> str | None:
            value = unquote(raw)
            if not _DIGEST_RE.match(value):
                self._error(HTTPStatus.BAD_REQUEST, f"not a sha256 digest: {raw!r}")
                return None
            return value

        # -- routing ----------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802 - stdlib method name
            split = urlsplit(self.path)
            path, query = split.path, parse_qs(split.query)

            for pattern, handler in (
                (_SUBJECT_LATEST_RE, self._handle_latest),
                (_SUBJECT_HISTORY_RE, self._handle_history),
                (_SUBJECT_POLICY_RE, lambda digest: self._handle_policy(digest, query)),
                (_CONTRACT_RE, self._handle_contract),
                (_EVIDENCE_RE, self._handle_evidence),
            ):
                match = pattern.match(path)
                if match:
                    handler(match.group(1))
                    return
            self._error(HTTPStatus.NOT_FOUND, "no such route")

        def do_POST(self) -> None:  # noqa: N802 - stdlib method name
            if urlsplit(self.path).path != _RESOLVE_PATH:
                self._error(HTTPStatus.NOT_FOUND, "no such route")
                return
            self._handle_resolve()

        # -- handlers -----------------------------------------------------------

        def _handle_latest(self, raw_digest: str) -> None:
            digest = self._digest_or_none(raw_digest)
            if digest is None:
                return
            observation = registry.latest(digest)
            if observation is None:
                self._error(HTTPStatus.NOT_FOUND, "no observation for subject")
                return
            self._send_json(HTTPStatus.OK, observation.to_json())

        def _handle_history(self, raw_digest: str) -> None:
            digest = self._digest_or_none(raw_digest)
            if digest is None:
                return
            observations = registry.history(digest)
            self._send_json(HTTPStatus.OK, {"observations": [o.to_json() for o in observations]})

        def _handle_policy(self, raw_digest: str, query: dict[str, list[str]]) -> None:
            digest = self._digest_or_none(raw_digest)
            if digest is None:
                return
            intents = query.get("intent") or ["retry"]
            intent = intents[0]
            if not intent or len(intent) > MAX_INTENT_LENGTH:
                self._error(HTTPStatus.BAD_REQUEST, f"intent must be 1-{MAX_INTENT_LENGTH} characters")
                return
            result = registry.policy(digest, intent=intent)
            self._send_json(HTTPStatus.OK, result.to_json())

        def _handle_contract(self, raw_digest: str) -> None:
            digest = self._digest_or_none(raw_digest)
            if digest is None:
                return
            try:
                contract = registry.get_contract(digest)
            except (OSError, ValueError):
                self._error(HTTPStatus.NOT_FOUND, "no contract for digest")
                return
            self._send_json(HTTPStatus.OK, contract)

        def _handle_evidence(self, raw_digest: str) -> None:
            digest = self._digest_or_none(raw_digest)
            if digest is None:
                return
            try:
                raw = registry.get_evidence(digest)
            except (OSError, ValueError):
                self._error(HTTPStatus.NOT_FOUND, "no evidence for digest")
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _drain(self, total: int, *, chunk_size: int = 65536) -> None:
            """Read and discard up to `total` bytes without ever holding
            more than one chunk in memory -- lets a client that already
            started sending an oversized body finish cleanly (no
            client-visible broken pipe) instead of racing the server's
            response against the client's still-in-flight write."""
            remaining = total
            while remaining > 0:
                chunk = self.rfile.read(min(chunk_size, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)

        def _read_bounded_json_body(self) -> Json | None:
            length_header = self.headers.get("Content-Length")
            if length_header is None or not length_header.isdigit():
                self._error(HTTPStatus.LENGTH_REQUIRED, "Content-Length required")
                return None
            length = int(length_header)
            if length > MAX_BODY_BYTES:
                # Bounded regardless of how large `length` claims to be: never buffer more
                # than one chunk at a time, and never drain more than 2x the real cap, so a
                # deliberately huge Content-Length can't turn this into its own DoS.
                self._drain(min(length, MAX_BODY_BYTES * 2))
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"body exceeds {MAX_BODY_BYTES} bytes")
                return None
            raw_body = self.rfile.read(length)
            try:
                return json.loads(raw_body)
            except json.JSONDecodeError:
                self._error(HTTPStatus.BAD_REQUEST, "body is not valid JSON")
                return None

        def _handle_resolve(self) -> None:
            body = self._read_bounded_json_body()
            if body is None:
                return
            if not isinstance(body, dict):
                self._error(HTTPStatus.BAD_REQUEST, "body must be a JSON object")
                return

            tool_definition = body.get("tool_definition")
            package_or_endpoint = body.get("package_or_endpoint")
            protocol_revision = body.get("protocol_revision")
            if not isinstance(tool_definition, dict):
                self._error(HTTPStatus.BAD_REQUEST, "'tool_definition' must be an object")
                return
            if not isinstance(package_or_endpoint, str) or not package_or_endpoint:
                self._error(HTTPStatus.BAD_REQUEST, "'package_or_endpoint' must be a non-empty string")
                return
            if not isinstance(protocol_revision, str) or not protocol_revision:
                self._error(HTTPStatus.BAD_REQUEST, "'protocol_revision' must be a non-empty string")
                return
            for field in _STRING_OR_NULL_FIELDS:
                value = body.get(field)
                if value is not None and not isinstance(value, str):
                    self._error(HTTPStatus.BAD_REQUEST, f"{field!r} must be a string or null")
                    return

            try:
                digest = registry.resolve(
                    registry_name=body.get("registry_name"),
                    registry_version=body.get("registry_version"),
                    package_or_endpoint=package_or_endpoint,
                    runtime_artifact_digest=body.get("runtime_artifact_digest"),
                    protocol_revision=protocol_revision,
                    tool_definition=tool_definition,
                )
            except RegistryError as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            self._send_json(HTTPStatus.OK, {"subject_digest": digest})

    return RegistryRequestHandler


def make_server(registry: Registry, *, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """A ready-to-`serve_forever()` server bound to `registry`. `port=0`
    (the default) asks the OS for a free port; read it back from
    `server.server_address[1]`."""
    return ThreadingHTTPServer((host, port), make_handler(registry))


__all__ = ["MAX_BODY_BYTES", "make_handler", "make_server"]
