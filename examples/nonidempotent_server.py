"""Synthetic stateful MCP target for the retry-gate demonstration."""

from __future__ import annotations

from mcp import types
from mcp.client import Client
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel

from babi.canon import Json
from babi.execute.wire import dump_model
from babi.subject import server_subject, tool_subject


CLIENT_INFO = types.Implementation(name="babi-demo", version="0.1.0")


class Record(BaseModel):
    id: str
    name: str


def build_server(state: list[Record] | None = None) -> MCPServer:
    records = state if state is not None else []
    server = MCPServer(name="nonidempotent-demo", version="0.1.0")

    @server.tool(annotations=types.ToolAnnotations(read_only_hint=False, idempotent_hint=True))
    async def create_record(name: str) -> Record:
        record = Record(id=str(len(records)), name=name)
        records.append(record)
        return record

    @server.tool(annotations=types.ToolAnnotations(read_only_hint=True))
    async def list_records() -> list[Record]:
        return list(records)

    return server


async def discovery_bundle(client: Client) -> dict[str, Json]:
    tools = (await client.list_tools()).tools
    catalog = sorted((dump_model(tool) for tool in tools), key=lambda tool: tool["name"])
    server_digest = server_subject(
        registry_name=None,
        registry_version=None,
        package_or_endpoint="examples/nonidempotent_server.py",
        runtime_artifact_digest=None,
        protocol_revision=client.protocol_version,
    )
    subjects = {tool["name"]: tool_subject(server_digest, tool) for tool in catalog}
    return {
        "kind": "discover/v0",
        "transport": "in-process",
        "package_or_endpoint": "examples/nonidempotent_server.py",
        "negotiated_protocol_revision": client.protocol_version,
        "runtime_digests": {},
        "server_subject": server_digest,
        "tool_subjects": subjects,
        "tools_list": catalog,
        "run": {"target_name": "nonidempotent-demo"},
    }
