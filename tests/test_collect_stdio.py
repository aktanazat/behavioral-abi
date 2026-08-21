"""Integration test: babi.collect.discover + build_discover_bundle against a
real subprocess speaking MCP, using the generic fake fixture server. This is
the only place a live protocol round trip is exercised end to end.
"""

from __future__ import annotations

import sys
from pathlib import Path

from mcp import types

from babi.collect import build_discover_bundle, discover
from babi.target import TargetSpec

FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "fake_server.py"
_CLIENT_INFO = types.Implementation(name="babi-test", version="0.0.0")


def _fixture_target() -> TargetSpec:
    return TargetSpec(
        name="fake",
        transport="stdio",
        command=[sys.executable, str(FIXTURE_SERVER)],
        args=[],
        env={},
        network="bridge",
        package_or_endpoint="fake-fixture-server",
    )


async def test_discover_round_trips_tool_catalog():
    discovery = await discover(_fixture_target(), client_info=_CLIENT_INFO)
    assert discovery.server_info is not None
    assert discovery.server_info.name == "fake-fixture-server"
    assert sorted(tool.name for tool in discovery.tools) == ["mirror", "ping"]


async def test_discover_captures_annotations_and_schemas_verbatim():
    discovery = await discover(_fixture_target(), client_info=_CLIENT_INFO)
    by_name = {tool.name: tool for tool in discovery.tools}

    ping = by_name["ping"]
    assert ping.annotations is not None
    assert ping.annotations.read_only_hint is True
    assert ping.annotations.idempotent_hint is True
    assert ping.input_schema["properties"]["value"]["type"] == "integer"

    mirror = by_name["mirror"]
    assert mirror.annotations is None


async def test_build_discover_bundle_has_the_lint_shape():
    discovery = await discover(_fixture_target(), client_info=_CLIENT_INFO)
    bundle = build_discover_bundle(discovery, runtime_digests={})

    assert bundle["kind"] == "discover/v0"
    assert set(bundle["tool_subjects"]) == {"mirror", "ping"}
    assert bundle["server_subject"].startswith("sha256:")
    assert all(digest.startswith("sha256:") for digest in bundle["tool_subjects"].values())


async def test_repeated_discovery_produces_the_same_subject_digests():
    """Phase 1 exit bar: the same target produces the same digest and
    evidence layout twice (RUBRIC.md, "Phase 1 threshold")."""
    first = await discover(_fixture_target(), client_info=_CLIENT_INFO)
    second = await discover(_fixture_target(), client_info=_CLIENT_INFO)

    bundle_first = build_discover_bundle(first, runtime_digests={"note": "run 1"})
    bundle_second = build_discover_bundle(second, runtime_digests={"note": "run 2"})

    assert bundle_first["server_subject"] == bundle_second["server_subject"]
    assert bundle_first["tool_subjects"] == bundle_second["tool_subjects"]
