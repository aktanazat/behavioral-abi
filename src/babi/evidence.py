"""Evidence-directory construction: canonical tool catalog, subject digest, and
run metadata, built uniformly from whatever the SDK returned.

Nothing in this module branches on a tool or server name. Every document is
produced by applying the same fixed transform to the SDK's response objects.
"""

from __future__ import annotations

import platform
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

from mcp import types

from babi.canonical import canonical_json_bytes, digest_of

_BABI_VERSION = _pkg_version("babi")
_MCP_SDK_VERSION = _pkg_version("mcp")


def _dump(model: Any) -> dict[str, Any]:
    """Wire-shaped (camelCase, no-null) dict for one pydantic MCP model."""
    return model.model_dump(by_alias=True, exclude_none=True, mode="json")


def canonical_tool_catalog(tools: Sequence[types.Tool]) -> list[dict[str, Any]]:
    """Canonical tool-catalog artifact.

    Every tool's full wire representation (name, description, inputSchema,
    outputSchema, annotations, _meta, and any other field the SDK exposes),
    sorted by name so the artifact — and any digest over it — is independent
    of the page order a server happened to return.
    """
    dumped = [_dump(tool) for tool in tools]
    return sorted(dumped, key=lambda tool: tool["name"])


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """The three canonical documents written to one evidence directory."""

    tools: list[dict[str, Any]]
    subject: dict[str, Any]
    run: dict[str, Any]

    def write(self, directory: Path) -> None:
        """Write `tools.json`, `subject.json`, and `run.json` into `directory`."""
        directory.mkdir(parents=True, exist_ok=True)
        _write_canonical(directory / "tools.json", self.tools)
        _write_canonical(directory / "subject.json", self.subject)
        _write_canonical(directory / "run.json", self.run)


def _write_canonical(path: Path, value: Any) -> None:
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def build_evidence(
    *,
    command: str,
    args: Sequence[str],
    initialize_result: types.InitializeResult,
    tools: Sequence[types.Tool],
    started_at: datetime,
    finished_at: datetime,
) -> EvidenceBundle:
    """Build the tools/subject/run documents for one collected session.

    `started_at`/`finished_at` land only in `run`; they never reach the
    subject payload, so the subject digest is stable across repeated runs of
    the same target even though every run happens at a different time.
    """
    tool_catalog = canonical_tool_catalog(tools)
    tools_digest = digest_of(tool_catalog)

    resolved_command = {"command": command, "args": list(args)}
    server_info = _dump(initialize_result.server_info)

    subject_payload = {
        "resolved_command": resolved_command,
        "protocol_version": initialize_result.protocol_version,
        "server_info": server_info,
        "tools_digest": tools_digest,
        "tool_count": len(tool_catalog),
    }
    subject_digest = digest_of(subject_payload)
    subject = {"subject_digest": subject_digest, **subject_payload}

    run = {
        "subject_digest": subject_digest,
        "tools_digest": tools_digest,
        "tool_count": len(tool_catalog),
        "resolved_command": resolved_command,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "initialize_result": _dump(initialize_result),
        "babi_version": _BABI_VERSION,
        "mcp_sdk_version": _MCP_SDK_VERSION,
        "python_version": platform.python_version(),
    }
    return EvidenceBundle(tools=tool_catalog, subject=subject, run=run)
