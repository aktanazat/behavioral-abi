"""Collector: transport-agnostic MCP discovery.

Connects to one target (`babi.target.TargetSpec`) over MCP stdio or MCP
Streamable HTTP, negotiates a protocol revision, and captures its full tool
catalog verbatim -- the same fixed transform for every server, branching
only on transport kind (an allowed protocol-adapter distinction;
RUBRIC.md's "Allowed" list), never on a tool or server name.
"""

from __future__ import annotations

from babi.collect.bundle import DISCOVER_KIND, build_discover_bundle, canonical_tool_catalog
from babi.collect.discover import Discovery, discover
from babi.collect.runtime import gather_runtime_digests, npm_package_integrity, npm_package_version

__all__ = [
    "DISCOVER_KIND",
    "Discovery",
    "build_discover_bundle",
    "canonical_tool_catalog",
    "discover",
    "gather_runtime_digests",
    "npm_package_integrity",
    "npm_package_version",
]
