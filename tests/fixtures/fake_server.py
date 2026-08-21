"""Fake MCP stdio server used only to exercise protocol behavior in tests.

Tool names and shapes here are synthetic placeholders, not modeled on any
real MCP server, vendor, or tool.
"""

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer(name="fake-fixture-server", version="0.0.1")


@server.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def ping(value: int) -> int:
    """Return the given integer unchanged."""
    return value


@server.tool()
def mirror(text: str, times: int = 1) -> str:
    """Repeat text `times` times."""
    return text * times


if __name__ == "__main__":
    server.run(transport="stdio")
