"""Transport-agnostic MCP discovery: connect, negotiate, capture everything
the server reports about itself and its tools, verbatim. No branch here is
keyed on a tool name, a server name, or any other target-specific detail --
the only inputs are a target (how to connect) and the client's own
self-identification.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from mcp import types
from mcp.client import Client, Transport
from mcp.client.stdio import StdioServerParameters, stdio_client

from babi.target import TargetSpec


@dataclass(frozen=True, slots=True)
class Discovery:
    """Everything one DISCOVER session observed about a live server."""

    target: TargetSpec
    negotiated_protocol_revision: str
    server_info: types.Implementation | None
    tools: tuple[types.Tool, ...]
    client_info: types.Implementation
    started_at: datetime
    finished_at: datetime


def _transport(target: TargetSpec) -> Transport | str:
    if target.transport == "stdio":
        argv = [*(target.command or []), *target.args]
        params = StdioServerParameters(command=argv[0], args=argv[1:], env=dict(target.env) or None)
        return stdio_client(params)
    assert target.endpoint is not None  # TargetSpec's validator guarantees this whenever transport isn't stdio
    return target.endpoint


async def discover(target: TargetSpec, *, client_info: types.Implementation) -> Discovery:
    """Connect to `target`, negotiate a protocol revision, and capture its
    full tool catalog. The connection is closed before this returns."""
    started_at = datetime.now(timezone.utc)
    async with Client(server=_transport(target), mode="auto", client_info=client_info) as client:
        tools = await _list_all_tools(client)
        return Discovery(
            target=target,
            negotiated_protocol_revision=client.protocol_version,
            server_info=client.server_info,
            tools=tools,
            client_info=client_info,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
        )


async def _list_all_tools(client: Client) -> tuple[types.Tool, ...]:
    """Page through `tools/list` until the server stops returning a cursor."""
    tools: list[types.Tool] = []
    cursor: str | None = None
    while True:
        page = await client.list_tools(cursor=cursor)
        tools.extend(page.tools)
        cursor = page.next_cursor
        if cursor is None:
            return tuple(tools)


__all__ = ["Discovery", "discover"]
