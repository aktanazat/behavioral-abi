"""Structural proof of RUBRIC.md's "no per-tool logic" boundary, scoped to
this package: no `.py` file under `src/babi/planner` may contain a string
literal equal to a real (or realistically-shaped) discovered MCP tool
name. Mirrors `babi.lint`'s check (c) -- re-implemented self-containedly
here rather than importing its private helpers, so this package's tests
never depend on another slice's internals, and so this test also works
before/without any `evidence/discover-v0` bundle existing on disk.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

PLANNER_SRC = Path(__file__).parent.parent.parent / "src" / "babi" / "planner"
REPO_ROOT = Path(__file__).parent.parent.parent

# Realistic multi-word, underscore-joined tool names -- the exact shape a
# real `tools/list` entry takes (see tests/fixtures/fake_server.py and the
# names actually captured under evidence/sha256/** by the filesystem and
# memory targets). None of these is a generic English word on its own;
# each is a specific operation name a per-tool implementation would need
# to special-case.
_CURATED_REALISTIC_TOOL_NAMES = frozenset(
    {
        "create_issue",
        "get_issue",
        "delete_repository",
        "list_directory",
        "create_directory",
        "directory_tree",
        "get_file_info",
        "list_allowed_directories",
        "list_directory_with_sizes",
        "move_file",
        "read_file",
        "read_media_file",
        "read_multiple_files",
        "read_text_file",
        "search_files",
        "write_file",
        "edit_file",
        "add_observations",
        "create_entities",
        "create_relations",
        "delete_entities",
        "delete_observations",
        "delete_relations",
        "open_nodes",
        "read_graph",
        "search_nodes",
        "refund_payment",
        "create_customer",
        "delete_customer",
    }
)


def _string_literals(py_path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _discovered_tool_names_from_evidence(root: Path) -> set[str]:
    evidence_dir = root / "evidence" / "sha256"
    if not evidence_dir.is_dir():
        return set()
    names: set[str] = set()
    for blob_path in evidence_dir.glob("*/*"):
        try:
            obj = json.loads(blob_path.read_text())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict) and obj.get("kind") == "discover/v0":
            tool_subjects = obj.get("tool_subjects")
            if isinstance(tool_subjects, dict):
                names.update(key for key in tool_subjects if isinstance(key, str))
    return names


def _planner_py_files() -> list[Path]:
    assert PLANNER_SRC.is_dir(), f"expected planner package at {PLANNER_SRC}"
    return sorted(PLANNER_SRC.rglob("*.py"))


def test_planner_package_actually_has_source_files() -> None:
    files = _planner_py_files()
    assert len(files) >= 5


def test_no_curated_realistic_tool_name_literal_in_planner_source() -> None:
    violations = []
    for py_path in _planner_py_files():
        for lineno, value in _string_literals(py_path):
            if value in _CURATED_REALISTIC_TOOL_NAMES:
                violations.append(f"{py_path}:{lineno}: {value!r}")
    assert violations == []


def test_no_actually_discovered_tool_name_literal_in_planner_source() -> None:
    """Extra assurance against whatever this repo's own evidence store has
    genuinely captured (filesystem/memory targets) -- skipped gracefully
    if no evidence exists yet rather than failing on repo state this
    package does not own."""
    discovered = _discovered_tool_names_from_evidence(REPO_ROOT)
    if not discovered:
        return
    violations = []
    for py_path in _planner_py_files():
        for lineno, value in _string_literals(py_path):
            if value in discovered:
                violations.append(f"{py_path}:{lineno}: {value!r}")
    assert violations == []


def test_heuristic_verb_words_are_not_by_themselves_operation_names() -> None:
    """The heuristics table's own words (single generic English verbs) are
    exactly what makes it *not* per-tool logic: none of them, alone, is a
    real tool name -- only ever a substring/token of one."""
    from babi.planner.heuristics import HEURISTIC_TABLE, HeuristicDomain

    for rule in HEURISTIC_TABLE:
        if rule.domain is HeuristicDomain.OPERATION:
            words = rule.pattern.pattern.strip("^$()").split("|")
            for word in words:
                assert word not in _CURATED_REALISTIC_TOOL_NAMES
                assert "_" not in word
