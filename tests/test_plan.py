import pytest
from pydantic import ValidationError

from babi.plan import DependencyEdge, ProbePlan, ProbePrimitive, RationaleEntry, RationaleProvenance


def _minimal_plan(**overrides: object) -> dict:
    base = {
        "subject_digest": "sha256:" + "a" * 64,
        "cleanup_budget": 1,
        "steps": [
            {
                "id": "discover",
                "primitive": "DISCOVER",
                "tool_subject": "sha256:" + "b" * 64,
            }
        ],
    }
    base.update(overrides)
    return base


def test_minimal_plan_validates() -> None:
    plan = ProbePlan.model_validate(_minimal_plan())
    assert plan.steps[0].primitive is ProbePrimitive.DISCOVER
    assert plan.prerequisites == []
    assert plan.rationale_evidence == []


def test_all_twelve_primitives_accepted() -> None:
    names = [
        "DISCOVER",
        "INSTANTIATE",
        "CALL_ONCE",
        "READ_BACK",
        "REPEAT",
        "RETRY_AFTER_INTERRUPT",
        "PARALLEL_DOUBLE_FIRE",
        "VERIFY_WITH_TIMING",
        "PERTURB_AND_UNDO",
        "BOUNDARY_MUTATION_FUZZ",
        "CROSS_CALL_CONSISTENCY",
        "PRECONDITION_REVOCATION",
    ]
    assert [p.value for p in ProbePrimitive] == names


def test_unknown_primitive_rejected() -> None:
    plan = _minimal_plan(steps=[{"id": "s", "primitive": "DELETE_EVERYTHING", "tool_subject": "x"}])
    with pytest.raises(ValidationError):
        ProbePlan.model_validate(plan)


def test_extra_field_forbidden_on_plan() -> None:
    with pytest.raises(ValidationError):
        ProbePlan.model_validate(_minimal_plan(unexpected_field="nope"))


def test_extra_field_forbidden_on_step() -> None:
    plan = _minimal_plan(
        steps=[
            {
                "id": "s",
                "primitive": "DISCOVER",
                "tool_subject": "sha256:" + "b" * 64,
                "unexpected": True,
            }
        ]
    )
    with pytest.raises(ValidationError):
        ProbePlan.model_validate(plan)


def test_negative_cleanup_budget_rejected() -> None:
    with pytest.raises(ValidationError):
        ProbePlan.model_validate(_minimal_plan(cleanup_budget=-1))


def test_rationale_provenance_only_allows_schema_overlap_or_model_pass() -> None:
    with pytest.raises(ValidationError):
        RationaleEntry.model_validate(
            {"edge": {"from_step": "a", "to_step": "b"}, "provenance": "handwritten"}
        )
    for provenance in ("schema-overlap", "model-pass"):
        entry = RationaleEntry.model_validate(
            {"edge": {"from_step": "a", "to_step": "b"}, "provenance": provenance}
        )
        assert entry.provenance is RationaleProvenance(provenance)


def test_plan_with_justified_dependency_edge() -> None:
    plan = ProbePlan.model_validate(
        _minimal_plan(
            steps=[
                {"id": "a", "primitive": "DISCOVER", "tool_subject": "sha256:" + "b" * 64},
                {
                    "id": "b",
                    "primitive": "CALL_ONCE",
                    "tool_subject": "sha256:" + "b" * 64,
                    "depends_on": ["a"],
                },
            ],
            rationale_evidence=[
                {"edge": {"from_step": "a", "to_step": "b"}, "provenance": "schema-overlap"}
            ],
        )
    )
    edge = DependencyEdge(from_step="a", to_step="b")
    assert plan.rationale_evidence[0].edge == edge
