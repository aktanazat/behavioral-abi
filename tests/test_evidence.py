"""babi.evidence: subject-digest determinism and content sensitivity.

These construct `mcp.types.Tool`/`InitializeResult` objects directly so the
digest-stability properties are tested without needing a live subprocess;
`test_collect_stdio.py` covers the real end-to-end protocol round trip.
"""

import json
from datetime import datetime, timezone

from mcp import types

from babi.evidence import build_evidence, canonical_tool_catalog


def _initialize_result() -> types.InitializeResult:
    return types.InitializeResult(
        protocol_version="2025-11-25",
        capabilities=types.ServerCapabilities(),
        server_info=types.Implementation(name="fixture-server", version="1.0.0"),
    )


def _tool(
    name: str,
    *,
    read_only: bool | None = None,
    output_schema: dict | None = None,
) -> types.Tool:
    annotations = types.ToolAnnotations(read_only_hint=read_only) if read_only is not None else None
    return types.Tool(
        name=name,
        description=f"Synthetic fixture tool {name}.",
        input_schema={"type": "object", "properties": {}},
        output_schema=output_schema,
        annotations=annotations,
    )


def _build(tools, *, started_at=None, finished_at=None, init=None):
    now = datetime.now(timezone.utc)
    return build_evidence(
        command="fake",
        args=["--flag"],
        initialize_result=init or _initialize_result(),
        tools=tools,
        started_at=started_at or now,
        finished_at=finished_at or now,
    )


def test_subject_digest_is_stable_across_timestamps():
    tools = [_tool("alpha"), _tool("beta")]
    init = _initialize_result()
    bundle_a = _build(
        tools,
        init=init,
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        finished_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
    )
    bundle_b = _build(
        tools,
        init=init,
        started_at=datetime(2099, 6, 6, tzinfo=timezone.utc),
        finished_at=datetime(2099, 6, 6, 0, 5, 0, tzinfo=timezone.utc),
    )
    assert bundle_a.subject["subject_digest"] == bundle_b.subject["subject_digest"]
    assert bundle_a.subject["tools_digest"] == bundle_b.subject["tools_digest"]
    assert bundle_a.run["started_at"] != bundle_b.run["started_at"]


def test_subject_digest_is_stable_across_tool_order():
    init = _initialize_result()
    forward = [_tool("alpha"), _tool("beta"), _tool("gamma")]
    backward = list(reversed(forward))

    bundle_forward = _build(forward, init=init)
    bundle_backward = _build(backward, init=init)

    assert bundle_forward.subject["subject_digest"] == bundle_backward.subject["subject_digest"]
    assert bundle_forward.tools == bundle_backward.tools


def test_subject_digest_changes_when_annotation_changes():
    init = _initialize_result()
    bundle_read_only = _build([_tool("alpha", read_only=True)], init=init)
    bundle_mutating = _build([_tool("alpha", read_only=False)], init=init)
    assert bundle_read_only.subject["subject_digest"] != bundle_mutating.subject["subject_digest"]


def test_subject_digest_changes_when_output_schema_changes():
    init = _initialize_result()
    int_schema = {"type": "object", "properties": {"result": {"type": "integer"}}}
    str_schema = {"type": "object", "properties": {"result": {"type": "string"}}}
    bundle_int = _build([_tool("alpha", output_schema=int_schema)], init=init)
    bundle_str = _build([_tool("alpha", output_schema=str_schema)], init=init)
    assert bundle_int.subject["subject_digest"] != bundle_str.subject["subject_digest"]


def test_subject_digest_changes_when_tool_set_changes():
    init = _initialize_result()
    bundle_two = _build([_tool("alpha"), _tool("beta")], init=init)
    bundle_three = _build([_tool("alpha"), _tool("beta"), _tool("gamma")], init=init)
    assert bundle_two.subject["subject_digest"] != bundle_three.subject["subject_digest"]


def test_canonical_tool_catalog_sorted_by_name_and_wire_shaped():
    catalog = canonical_tool_catalog([_tool("zebra"), _tool("alpha")])
    assert [tool["name"] for tool in catalog] == ["alpha", "zebra"]
    assert "inputSchema" in catalog[0]
    assert "input_schema" not in catalog[0]


def test_evidence_bundle_write_creates_tools_subject_run(tmp_path):
    bundle = _build([_tool("alpha")])
    out_dir = tmp_path / "evidence"
    bundle.write(out_dir)

    for name in ("tools.json", "subject.json", "run.json"):
        assert (out_dir / name).exists()

    subject_on_disk = json.loads((out_dir / "subject.json").read_text())
    assert subject_on_disk["subject_digest"] == bundle.subject["subject_digest"]
    run_on_disk = json.loads((out_dir / "run.json").read_text())
    assert run_on_disk["subject_digest"] == bundle.subject["subject_digest"]
    assert "started_at" in run_on_disk
