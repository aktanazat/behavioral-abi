"""Shared fixtures for executor tests: an in-process, stateful fake MCP
server (no subprocess, no real transport -- `mcp.client.Client(server=...)`'s
own in-memory connector) plus a matching discovery-bundle builder.

Tool names and shapes here are synthetic placeholders, not modeled on any
real MCP server, vendor, or tool -- same convention as
`tests/fixtures/fake_server.py`.
"""

from __future__ import annotations

import anyio
import pytest
from mcp import types
from mcp.client import Client
from mcp.server.mcpserver import MCPServer

from babi.canon import Json
from babi.execute.wire import dump_model
from babi.subject import server_subject, tool_subject

CLIENT_INFO = types.Implementation(name="babi-test", version="0.0.0")


def build_stateful_server(*, response_delay: float = 0.0) -> tuple[MCPServer, dict[str, list]]:
    """A fresh server with independent, mutable state:

    - `create_item(name)`: unconditionally appends to `items` -- calling it
      twice with the same argument creates two distinct items (a duplicate-
      creation handler).
    - `create_item_dedup(name)`: appends only if no existing item already
      has that name (a deduping handler) -- the same repeated call is a
      no-op the second time.
    - `list_items()`: the read-back target for both create tools; its
      output items share an `id`/`name` shape with their responses, so
      schema-overlap binding treats it as their effect channel.
    - `log_event()` / `list_logs()`: a `logs` resource that grows by one
      plain string on every `create_item*` call unconditionally, and a
      listing tool for it, deliberately shaped (plain strings, no
      `id`/`name` fields) so it is never confused with `items` -- this is
      the ancillary side channel RFC 9110 SS9.2.2 permits to vary freely.

    `response_delay` (seconds) is awaited *after* the mutation but *before*
    returning, so a small caller-side interrupt budget can reliably abandon
    the response wait while the mutation has already landed -- the
    deterministic version of Stripe's "ambiguous failure" scenario.
    """
    state: dict[str, list] = {"items": [], "logs": []}
    server = MCPServer(name="fake-stateful-fixture", version="0.0.1")

    def _log(call: str) -> None:
        state["logs"].append(f"{call} at call #{len(state['logs'])}")

    @server.tool(annotations=types.ToolAnnotations(read_only_hint=False, idempotent_hint=False))
    async def create_item(name: str) -> dict[str, object]:
        item_id = str(len(state["items"]))
        state["items"].append({"id": item_id, "name": name})
        _log(f"create_item({name!r})")
        await anyio.sleep(response_delay)
        return {"id": item_id, "name": name}

    @server.tool(annotations=types.ToolAnnotations(read_only_hint=False, idempotent_hint=True))
    async def create_item_dedup(name: str) -> dict[str, object]:
        _log(f"create_item_dedup({name!r})")
        for item in state["items"]:
            if item["name"] == name:
                await anyio.sleep(response_delay)
                return {"id": item["id"], "name": name}
        item_id = str(len(state["items"]))
        state["items"].append({"id": item_id, "name": name})
        await anyio.sleep(response_delay)
        return {"id": item_id, "name": name}

    @server.tool(annotations=types.ToolAnnotations(read_only_hint=True))
    async def list_items() -> list[dict[str, object]]:
        return list(state["items"])

    @server.tool(annotations=types.ToolAnnotations(read_only_hint=True))
    async def list_logs() -> list[str]:
        return list(state["logs"])

    return server, state


async def discovery_bundle_for(client: Client) -> dict[str, Json]:
    """A minimal but shape-correct `discover/v0`-style bundle for an
    already-connected in-process `client`, built the same way
    `babi.collect.bundle.build_discover_bundle` would from a real
    `Discovery` -- just without a `TargetSpec`, since in-process servers
    have no transport/endpoint of their own."""
    tools = (await client.list_tools()).tools
    tool_dumps = sorted((dump_model(tool) for tool in tools), key=lambda tool: tool["name"])

    subject = server_subject(
        registry_name=None,
        registry_version=None,
        package_or_endpoint="fake-stateful-fixture",
        runtime_artifact_digest=None,
        protocol_revision=client.protocol_version,
    )
    tool_subjects = {tool["name"]: tool_subject(subject, tool) for tool in tool_dumps}

    return {
        "kind": "discover/v0",
        "transport": "in-process",
        "package_or_endpoint": "fake-stateful-fixture",
        "negotiated_protocol_revision": client.protocol_version,
        "runtime_digests": {},
        "server_subject": subject,
        "tool_subjects": tool_subjects,
        "tools_list": tool_dumps,
    }


@pytest.fixture
def stateful_server_factory():
    """Yields `build_stateful_server`, so tests can request several
    independent servers (independent state) within one test."""
    return build_stateful_server
