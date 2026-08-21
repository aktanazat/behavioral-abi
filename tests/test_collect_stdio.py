"""Integration test: collect_stdio_session + build_evidence against a real
subprocess speaking MCP, using the generic fake fixture server. This is the
only place a live protocol round trip is exercised end to end.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from babi.evidence import build_evidence
from babi.mcp_stdio import collect_stdio_session

FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "fake_server.py"


async def test_collect_stdio_session_round_trips_tool_catalog():
    raw = await collect_stdio_session(sys.executable, [str(FIXTURE_SERVER)])
    assert raw.initialize_result.server_info.name == "fake-fixture-server"
    assert sorted(tool.name for tool in raw.tools) == ["mirror", "ping"]


async def test_collect_stdio_session_captures_annotations_and_schemas():
    raw = await collect_stdio_session(sys.executable, [str(FIXTURE_SERVER)])
    by_name = {tool.name: tool for tool in raw.tools}

    ping = by_name["ping"]
    assert ping.annotations is not None
    assert ping.annotations.read_only_hint is True
    assert ping.input_schema["properties"]["value"]["type"] == "integer"
    assert ping.output_schema is not None

    mirror = by_name["mirror"]
    assert mirror.annotations is None


async def test_collect_and_build_evidence_end_to_end(tmp_path):
    raw = await collect_stdio_session(sys.executable, [str(FIXTURE_SERVER)])
    bundle = build_evidence(
        command=sys.executable,
        args=[str(FIXTURE_SERVER)],
        initialize_result=raw.initialize_result,
        tools=raw.tools,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    out_dir = tmp_path / "evidence"
    bundle.write(out_dir)

    for name in ("tools.json", "subject.json", "run.json"):
        assert (out_dir / name).exists()
    assert bundle.subject["tool_count"] == 2

    tools_on_disk = json.loads((out_dir / "tools.json").read_text())
    assert [t["name"] for t in tools_on_disk] == ["mirror", "ping"]


async def test_repeated_collection_produces_the_same_digest_and_layout():
    """Phase 1 exit bar: the same target produces the same digest and
    evidence layout twice (RUBRIC.md, "Phase 1 threshold")."""
    first = await collect_stdio_session(sys.executable, [str(FIXTURE_SERVER)])
    second = await collect_stdio_session(sys.executable, [str(FIXTURE_SERVER)])

    bundle_first = build_evidence(
        command=sys.executable,
        args=[str(FIXTURE_SERVER)],
        initialize_result=first.initialize_result,
        tools=first.tools,
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        finished_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
    )
    bundle_second = build_evidence(
        command=sys.executable,
        args=[str(FIXTURE_SERVER)],
        initialize_result=second.initialize_result,
        tools=second.tools,
        started_at=datetime(2099, 12, 31, tzinfo=timezone.utc),
        finished_at=datetime(2099, 12, 31, 0, 5, 0, tzinfo=timezone.utc),
    )

    assert bundle_first.subject["subject_digest"] == bundle_second.subject["subject_digest"]
    assert bundle_first.tools == bundle_second.tools
