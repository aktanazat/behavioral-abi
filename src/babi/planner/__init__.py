"""Generic planner: deterministic JSON-Schema analysis, seeded value
generation, dependency-candidate inference, a constrained-model-pass
abstraction, and a plan builder -- none of it keyed on a tool or server
name (RUBRIC.md, "Scope rule: no per-tool logic"). See IMPLEMENTATION-PLAN.md
`### Planner` for the two-pass design this package implements: a
deterministic pass (this package's `schema`, `values`, `dependencies`
modules) and a constrained model pass (`model`) that only ever ranks or
fills in what the deterministic pass already proposed.
"""

from __future__ import annotations

from babi.planner.build import PlanBuilderConstraints, build_probe_plan, eligible_primitives
from babi.planner.dependencies import (
    DependencyCandidate,
    DeterministicCandidates,
    infer_all_candidates,
)
from babi.planner.heuristics import OperationClass, ValueCategory
from babi.planner.model import (
    MalformedModelResponseError,
    ModelInvocationError,
    ModelPassResult,
    ModelPassSpec,
    ModelPlanner,
    PlanAgreementReport,
    compare_plans,
)
from babi.planner.schema import CatalogEntry, FieldSchema, SchemaAnalysis, ToolAnalysis, analyze_catalog, analyze_tool
from babi.planner.values import TypedBindings, synthesize_arguments, synthesize_call

__all__ = [
    "CatalogEntry",
    "DependencyCandidate",
    "DeterministicCandidates",
    "FieldSchema",
    "MalformedModelResponseError",
    "ModelInvocationError",
    "ModelPassResult",
    "ModelPassSpec",
    "ModelPlanner",
    "OperationClass",
    "PlanAgreementReport",
    "PlanBuilderConstraints",
    "SchemaAnalysis",
    "ToolAnalysis",
    "TypedBindings",
    "ValueCategory",
    "analyze_catalog",
    "analyze_tool",
    "build_probe_plan",
    "compare_plans",
    "eligible_primitives",
    "infer_all_candidates",
    "synthesize_arguments",
    "synthesize_call",
]
