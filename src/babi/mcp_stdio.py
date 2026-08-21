"""Generic MCP stdio session collection: initialize + tools/list only.

Every server is driven through the same two SDK calls regardless of what
tools it advertises. There is no branch here keyed on a tool name, a server
name, or any other operation-specific detail; the only inputs are the
command to launch and the arguments/environment/cwd to launch it with.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client


@dataclass(frozen=True, slots=True)
class RawCollection:
    """Unprocessed SDK results from one stdio initialize + tools/list round trip."""

    initialize_result: types.InitializeResult
    tools: tuple[types.Tool, ...]


async def collect_stdio_session(
    command: str,
    args: Sequence[str] = (),
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> RawCollection:
    """Connect to `command` over MCP stdio, initialize, and list every tool page.

    `args`, `env`, and `cwd` describe how to launch the target process; they
    are opaque strings from babi's point of view, never inspected or
    special-cased by value.
    """
    server_params = StdioServerParameters(command=command, args=list(args), env=env, cwd=cwd)
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialize_result = await session.initialize()
            tools = await _list_all_tools(session)
    return RawCollection(initialize_result=initialize_result, tools=tools)


async def _list_all_tools(session: ClientSession) -> tuple[types.Tool, ...]:
    """Page through `tools/list` until the server stops returning a cursor."""
    tools: list[types.Tool] = []
    cursor: str | None = None
    while True:
        params = types.PaginatedRequestParams(cursor=cursor) if cursor is not None else None
        page = await session.list_tools(params=params)
        tools.extend(page.tools)
        cursor = page.next_cursor
        if cursor is None:
            return tuple(tools)
