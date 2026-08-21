"""discover/v0 evidence-bundle construction.

Builds the exact tagged document `babi.lint`'s no-per-tool-logic check (c)
scans for tool-name literals against: `{"kind": "discover/v0", ...,
"tool_subjects": {<tool name>: <tool_subject digest>}}`. Every field is
either wire data the server returned, an explicit input the caller already
had, or a digest computed from those two -- nothing here is looked up or
branched on a specific tool or server name.
"""

from __future__ import annotations

from datetime import datetime, timezone
from importlib.metadata import version as _pkg_version

from mcp import types
from pydantic import BaseModel

from babi.canon import Json
from babi.collect.discover import Discovery
from babi.subject import server_subject, tool_subject

DISCOVER_KIND = "discover/v0"

_MCP_SDK_VERSION = _pkg_version("mcp")
_BABI_VERSION = _pkg_version("babi")


def _dump(model: BaseModel) -> dict[str, Json]:
    """Wire-shaped (camelCase, no-null) dict for one pydantic MCP model."""
    return model.model_dump(by_alias=True, exclude_none=True, mode="json")


def canonical_tool_catalog(tools: tuple[types.Tool, ...]) -> list[dict[str, Json]]:
    """Every tool's full wire representation, sorted by name so the artifact
    -- and any digest over it -- is independent of tools/list page order."""
    return sorted((_dump(tool) for tool in tools), key=lambda tool: tool["name"])


def build_discover_bundle(
    discovery: Discovery,
    *,
    runtime_digests: dict[str, str | None],
    registry_name: str | None = None,
    registry_version: str | None = None,
) -> dict[str, Json]:
    """Build the `discover/v0` evidence document for one collected session.

    `captured_at`, `client_sdk`, and `runtime_digests` land in the bundle but
    never feed `server_subject`/`tool_subject`, so the same target produces
    the same subject digests on every run regardless of when it ran or which
    runtime artifact happened to be resolved that moment (RUBRIC.md Phase 1
    exit: "the same target produces the same digest ... twice").
    """
    tool_catalog = canonical_tool_catalog(discovery.tools)

    subject = server_subject(
        registry_name=registry_name,
        registry_version=registry_version,
        package_or_endpoint=discovery.target.package_or_endpoint,
        runtime_artifact_digest=runtime_digests.get("artifact_digest"),
        protocol_revision=discovery.negotiated_protocol_revision,
    )
    tool_subjects = {tool["name"]: tool_subject(subject, tool) for tool in tool_catalog}

    return {
        "kind": DISCOVER_KIND,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "transport": discovery.target.transport,
        "package_or_endpoint": discovery.target.package_or_endpoint,
        "negotiated_protocol_revision": discovery.negotiated_protocol_revision,
        "client_sdk": {
            "name": "mcp",
            "version": _MCP_SDK_VERSION,
            "language": "python",
            "babi_version": _BABI_VERSION,
            "client_info": _dump(discovery.client_info),
        },
        "runtime_digests": runtime_digests,
        "server_info": _dump(discovery.server_info) if discovery.server_info is not None else None,
        "tools_list": tool_catalog,
        "server_subject": subject,
        "tool_subjects": tool_subjects,
        "run": {
            "target_name": discovery.target.name,
            "started_at": discovery.started_at.isoformat(),
            "finished_at": discovery.finished_at.isoformat(),
            "tool_count": len(tool_catalog),
        },
    }


__all__ = ["DISCOVER_KIND", "build_discover_bundle", "canonical_tool_catalog"]
