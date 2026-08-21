import json
from pathlib import Path

from babi.evidence import EvidenceStore
from babi.lint import lint


def test_clean_empty_repo_has_no_violations(tmp_path: Path) -> None:
    assert lint(tmp_path) == []


def test_valid_target_passes(tmp_path: Path) -> None:
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    (targets_dir / "fs.json").write_text(
        json.dumps(
            {
                "name": "fs-target",
                "transport": "stdio",
                "command": ["mcp-server-filesystem", "/workspace"],
                "network": "none",
                "package_or_endpoint": "@modelcontextprotocol/server-filesystem",
            }
        )
    )
    assert lint(tmp_path) == []


def test_invalid_target_is_a_violation(tmp_path: Path) -> None:
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    (targets_dir / "bad.json").write_text(
        json.dumps(
            {
                "name": "fs-target",
                "transport": "stdio",
                "command": ["mcp-server-filesystem"],
                "endpoint": "https://example.test",  # both set -- invalid
                "network": "none",
                "package_or_endpoint": "@modelcontextprotocol/server-filesystem",
            }
        )
    )
    violations = lint(tmp_path)
    assert len(violations) == 1
    assert "bad.json" in violations[0]


def test_valid_sandbox_passes(tmp_path: Path) -> None:
    sandboxes_dir = tmp_path / "sandboxes"
    sandboxes_dir.mkdir()
    (sandboxes_dir / "fs.json").write_text(json.dumps({"target_label": "fs-target"}))
    assert lint(tmp_path) == []


def test_invalid_sandbox_is_a_violation(tmp_path: Path) -> None:
    sandboxes_dir = tmp_path / "sandboxes"
    sandboxes_dir.mkdir()
    (sandboxes_dir / "bad.json").write_text(
        json.dumps({"target_label": "fs-target", "expected_tool_behavior": "idempotent"})
    )
    violations = lint(tmp_path)
    assert len(violations) == 1
    assert "bad.json" in violations[0]


def _seed_plan_bundle(tmp_path: Path, *, rationale_evidence: list[dict]) -> None:
    store = EvidenceStore(root=tmp_path / "evidence")
    plan = {
        "subject_digest": "sha256:" + "a" * 64,
        "cleanup_budget": 1,
        "steps": [
            {"id": "discover", "primitive": "DISCOVER", "tool_subject": "sha256:" + "b" * 64},
            {
                "id": "call",
                "primitive": "CALL_ONCE",
                "tool_subject": "sha256:" + "b" * 64,
                "depends_on": ["discover"],
            },
        ],
        "rationale_evidence": rationale_evidence,
    }
    store.put_json({"kind": "plan/v0", "plan": plan})


def test_plan_with_justified_edge_passes(tmp_path: Path) -> None:
    _seed_plan_bundle(
        tmp_path,
        rationale_evidence=[
            {"edge": {"from_step": "discover", "to_step": "call"}, "provenance": "schema-overlap"}
        ],
    )
    assert lint(tmp_path) == []


def test_plan_with_unjustified_edge_is_a_violation(tmp_path: Path) -> None:
    _seed_plan_bundle(tmp_path, rationale_evidence=[])
    violations = lint(tmp_path)
    assert len(violations) == 1
    assert "call" in violations[0]
    assert "discover" in violations[0]


def _seed_discover_bundle(tmp_path: Path, tool_names: list[str]) -> None:
    store = EvidenceStore(root=tmp_path / "evidence")
    store.put_json(
        {
            "kind": "discover/v0",
            "tool_subjects": {name: "sha256:" + "c" * 64 for name in tool_names},
        }
    )


def test_seeded_per_tool_literal_is_caught(tmp_path: Path) -> None:
    _seed_discover_bundle(tmp_path, ["seeded_tool_name"])
    module_dir = tmp_path / "src" / "babi"
    module_dir.mkdir(parents=True)
    (module_dir / "smells.py").write_text('TOOL = "seeded_tool_name"\n')

    violations = lint(tmp_path)
    assert len(violations) == 1
    assert "smells.py" in violations[0]
    assert "seeded_tool_name" in violations[0]


def test_clean_source_with_discover_bundle_passes(tmp_path: Path) -> None:
    _seed_discover_bundle(tmp_path, ["some_tool"])
    module_dir = tmp_path / "src" / "babi"
    module_dir.mkdir(parents=True)
    (module_dir / "fine.py").write_text('TOOL = "totally_unrelated_name"\n')

    assert lint(tmp_path) == []


def test_literal_inside_tests_path_segment_is_exempt(tmp_path: Path) -> None:
    _seed_discover_bundle(tmp_path, ["seeded_tool_name"])
    tests_dir = tmp_path / "src" / "babi" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "fixture.py").write_text('TOOL = "seeded_tool_name"\n')

    assert lint(tmp_path) == []
