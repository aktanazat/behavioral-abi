"""No-per-tool-logic policy linter, run via `uv run babi lint`.

Three checks, all driven by generic schema/data, never a hardcoded tool
name:

(a) Every `targets/*.json` file validates against TargetSpec, and every
    `sandboxes/*.json` file validates against SandboxManifest.
(b) Every `plan/v0` evidence blob validates against ProbePlan, and every
    step's `depends_on` edge is backed by a `rationale_evidence` entry --
    an edge with no such entry is a handwritten, unjustified dependency.
(c) No `.py` file under `src/babi` (outside a `tests` path segment)
    contains a string literal equal to a tool name captured by any
    `discover/v0` evidence blob's `tool_subjects` mapping. Tool names are
    runtime data; they must never appear as code or configuration
    literals.

Evidence blobs are tagged JSON envelopes: `{"kind": "<name>/<version>", ...}`.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

from babi.manifest import SandboxManifest
from babi.plan import ProbePlan
from babi.target import TargetSpec

DISCOVER_KIND = "discover/v0"
PLAN_KIND = "plan/v0"


def _lint_json_directory(directory: Path, model: type[BaseModel]) -> list[str]:
    if not directory.is_dir():
        return []

    violations: list[str] = []
    for json_path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(json_path.read_text())
        except json.JSONDecodeError as error:
            violations.append(f"{json_path}: invalid JSON ({error})")
            continue
        try:
            model.model_validate(raw)
        except ValidationError as error:
            violations.append(f"{json_path}: invalid {model.__name__} ({error})")
    return violations


def _iter_evidence_blobs(root: Path) -> list[tuple[Path, dict]]:
    evidence_dir = root / "evidence" / "sha256"
    if not evidence_dir.is_dir():
        return []

    blobs: list[tuple[Path, dict]] = []
    for blob_path in sorted(evidence_dir.glob("*/*")):
        try:
            obj = json.loads(blob_path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(obj, dict) and "kind" in obj:
            blobs.append((blob_path, obj))
    return blobs


def _lint_plans(evidence_blobs: list[tuple[Path, dict]]) -> list[str]:
    violations: list[str] = []
    for blob_path, obj in evidence_blobs:
        if obj.get("kind") != PLAN_KIND:
            continue

        body = obj.get("plan", obj)
        try:
            plan = ProbePlan.model_validate(body)
        except ValidationError as error:
            violations.append(f"{blob_path}: invalid ProbePlan ({error})")
            continue

        justified_edges = {
            (entry.edge.from_step, entry.edge.to_step) for entry in plan.rationale_evidence
        }
        for step in plan.steps:
            for dependency in step.depends_on:
                if (dependency, step.id) not in justified_edges:
                    violations.append(
                        f"{blob_path}: step {step.id!r} depends on {dependency!r} with no "
                        "rationale_evidence entry (schema-overlap or model-pass provenance)"
                    )
    return violations


def _discovered_tool_names(evidence_blobs: list[tuple[Path, dict]]) -> set[str]:
    names: set[str] = set()
    for _, obj in evidence_blobs:
        if obj.get("kind") != DISCOVER_KIND:
            continue
        tool_subjects = obj.get("tool_subjects")
        if isinstance(tool_subjects, dict):
            names.update(key for key in tool_subjects if isinstance(key, str))
    return names


def _lint_no_per_tool_literals(root: Path, tool_names: set[str]) -> list[str]:
    if not tool_names:
        return []
    src_dir = root / "src" / "babi"
    if not src_dir.is_dir():
        return []

    violations: list[str] = []
    for py_path in sorted(src_dir.rglob("*.py")):
        if "tests" in py_path.parts:
            continue
        try:
            tree = ast.parse(py_path.read_text(), filename=str(py_path))
        except SyntaxError as error:
            violations.append(f"{py_path}: could not parse for lint ({error})")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in tool_names:
                    violations.append(
                        f"{py_path}:{node.lineno}: string literal {node.value!r} matches a "
                        "discovered tool name -- per-tool logic is forbidden"
                    )
    return violations


def lint(root: str | Path = ".") -> list[str]:
    """Run every no-per-tool-logic check under `root`; return violation strings."""
    root_path = Path(root)
    violations = _lint_json_directory(root_path / "targets", TargetSpec)
    violations += _lint_json_directory(root_path / "sandboxes", SandboxManifest)

    evidence_blobs = _iter_evidence_blobs(root_path)
    violations += _lint_plans(evidence_blobs)
    violations += _lint_no_per_tool_literals(root_path, _discovered_tool_names(evidence_blobs))

    return violations
