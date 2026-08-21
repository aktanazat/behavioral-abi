"""ModelPlanner tests use a real subprocess (a tiny fixture script under
tests/planner/fixtures/), the same black-box-through-the-public-API style
tests/test_collect_stdio.py uses for the MCP collector -- never calling
`ModelPlanner`'s private `_invoke`/`_validate` directly."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from babi.evidence import EvidenceStore
from babi.plan import ProbePlan
from babi.planner.dependencies import infer_all_candidates
from babi.planner.model import (
    MalformedModelResponseError,
    ModelInvocationError,
    ModelPassSpec,
    ModelPlanner,
    PlanAgreementReport,
    build_plan_envelope,
    compare_plans,
)
from babi.planner.schema import analyze_catalog

FIXTURES = Path(__file__).parent / "fixtures"

_CATALOG = [
    {
        "name": "make_widget",
        "description": "Create a new widget resource.",
        "inputSchema": {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]},
        "outputSchema": {"type": "object", "properties": {"widget_id": {"type": "string"}}, "required": ["widget_id"]},
    },
    {
        "name": "fetch_widget",
        "description": "Fetch a widget by id.",
        "inputSchema": {"type": "object", "properties": {"widget_id": {"type": "string"}}, "required": ["widget_id"]},
    },
]
_SUBJECTS = {"make_widget": "sha256:" + "a" * 64, "fetch_widget": "sha256:" + "b" * 64}
_SERVER_SUBJECT = "sha256:" + "f" * 64


def _candidates():
    return infer_all_candidates(analyze_catalog(_CATALOG, _SUBJECTS))


def _spec(fixture_name: str, **overrides) -> ModelPassSpec:
    base = dict(command=(sys.executable, str(FIXTURES / fixture_name)), model="fake-model-v1", temperature=0.0)
    base.update(overrides)
    return ModelPassSpec(**base)


def test_valid_model_response_validates_into_probeplan(tmp_path) -> None:
    evidence = EvidenceStore(root=tmp_path / "evidence")
    planner = ModelPlanner(_spec("fake_model_ok.py"), evidence=evidence)
    result = planner.run(server_subject=_SERVER_SUBJECT, tool_catalog=_CATALOG, deterministic_candidates=_candidates())

    assert isinstance(result.plan, ProbePlan)
    assert result.plan.subject_digest == _SERVER_SUBJECT
    assert result.plan.steps[0].tool_subject == _SERVER_SUBJECT
    assert result.plan.steps[1].tool_subject == _SUBJECTS["make_widget"]


def test_valid_model_response_persists_prompt_and_response_digests(tmp_path) -> None:
    evidence = EvidenceStore(root=tmp_path / "evidence")
    planner = ModelPlanner(_spec("fake_model_ok.py"), evidence=evidence)
    result = planner.run(server_subject=_SERVER_SUBJECT, tool_catalog=_CATALOG, deterministic_candidates=_candidates())

    assert result.prompt_digest.startswith("sha256:")
    assert result.response_digest.startswith("sha256:")
    stored_prompt = evidence.get(result.prompt_digest).decode("utf-8")
    assert "constrained model pass" in stored_prompt
    assert _SERVER_SUBJECT in stored_prompt  # the real catalog/context landed in the prompt
    stored_response = evidence.get(result.response_digest).decode("utf-8")
    assert _SERVER_SUBJECT in stored_response


def test_envelope_carries_model_and_prompt_provenance_as_plan_v0(tmp_path) -> None:
    evidence = EvidenceStore(root=tmp_path / "evidence")
    planner = ModelPlanner(_spec("fake_model_ok.py", model="model-x", temperature=0.7), evidence=evidence)
    result = planner.run(server_subject=_SERVER_SUBJECT, tool_catalog=_CATALOG, deterministic_candidates=_candidates())

    assert result.envelope["kind"] == "plan/v0"
    assert result.envelope["pass_kind"] == "model"
    assert result.envelope["model"] == "model-x"
    assert result.envelope["temperature"] == "0.7"  # stringified: canon-json-safe regardless of value
    assert result.envelope["prompt_digest"] == result.prompt_digest
    assert result.envelope["response_digest"] == result.response_digest
    assert result.envelope["plan"]["subject_digest"] == _SERVER_SUBJECT
    # The envelope itself must be persistable through the canonical path
    # (fractional temperature stringified, no raw scores anywhere in it).
    from babi.canon import canonical_json

    canonical_json(result.envelope)


def test_deterministic_pass_envelope_has_explicit_nulls_not_omitted_keys() -> None:
    plan = ProbePlan.model_validate(
        {
            "subject_digest": _SERVER_SUBJECT,
            "cleanup_budget": 0,
            "steps": [{"id": "discover", "primitive": "DISCOVER", "tool_subject": _SERVER_SUBJECT}],
        }
    )
    envelope = build_plan_envelope(plan, pass_kind="deterministic")
    assert envelope["pass_kind"] == "deterministic"
    assert envelope["model"] is None
    assert envelope["temperature"] is None
    assert envelope["prompt_digest"] is None
    assert envelope["response_digest"] is None
    assert set(envelope) == {"kind", "plan", "pass_kind", "model", "temperature", "prompt_digest", "response_digest"}


def test_unparseable_json_response_is_rejected(tmp_path) -> None:
    evidence = EvidenceStore(root=tmp_path / "evidence")
    planner = ModelPlanner(_spec("fake_model_unparseable.py"), evidence=evidence)
    with pytest.raises(MalformedModelResponseError) as excinfo:
        planner.run(server_subject=_SERVER_SUBJECT, tool_catalog=_CATALOG, deterministic_candidates=_candidates())
    assert "not valid JSON" in str(excinfo.value)
    assert excinfo.value.raw_response  # the offending text is preserved, not swallowed


def test_schema_invalid_json_response_is_rejected(tmp_path) -> None:
    evidence = EvidenceStore(root=tmp_path / "evidence")
    planner = ModelPlanner(_spec("fake_model_invalid_shape.py"), evidence=evidence)
    with pytest.raises(MalformedModelResponseError) as excinfo:
        planner.run(server_subject=_SERVER_SUBJECT, tool_catalog=_CATALOG, deterministic_candidates=_candidates())
    assert "ProbePlan" in str(excinfo.value)


def test_dependency_without_rationale_is_rejected_before_plan_persistence(tmp_path) -> None:
    evidence_root = tmp_path / "evidence"
    planner = ModelPlanner(
        _spec("fake_model_missing_rationale.py"),
        evidence=EvidenceStore(root=evidence_root),
    )
    with pytest.raises(MalformedModelResponseError, match="missing_rationale"):
        planner.run(
            server_subject=_SERVER_SUBJECT,
            tool_catalog=_CATALOG,
            deterministic_candidates=_candidates(),
        )
    assert not evidence_root.exists() or not any(evidence_root.rglob("*"))

def test_malformed_response_is_not_persisted_as_a_plan(tmp_path) -> None:
    """A rejected response must not leave a `plan/v0` envelope behind --
    only a genuinely valid ProbePlan gets one."""
    evidence_root = tmp_path / "evidence"
    evidence = EvidenceStore(root=evidence_root)
    planner = ModelPlanner(_spec("fake_model_unparseable.py"), evidence=evidence)
    with pytest.raises(MalformedModelResponseError):
        planner.run(server_subject=_SERVER_SUBJECT, tool_catalog=_CATALOG, deterministic_candidates=_candidates())
    assert not evidence_root.exists() or not any(evidence_root.rglob("*"))


def test_nonzero_exit_raises_model_invocation_error_not_malformed_response(tmp_path) -> None:
    evidence = EvidenceStore(root=tmp_path / "evidence")
    planner = ModelPlanner(_spec("fake_model_bad_exit.py"), evidence=evidence)
    with pytest.raises(ModelInvocationError) as excinfo:
        planner.run(server_subject=_SERVER_SUBJECT, tool_catalog=_CATALOG, deterministic_candidates=_candidates())
    assert "exited 1" in str(excinfo.value)


def test_no_embedded_provider_or_api_key_in_spec() -> None:
    """ModelPassSpec has no field for a provider identity or credential --
    the command argv and env are entirely caller-supplied."""
    spec = ModelPassSpec(command=("echo",), model="whatever")
    assert not hasattr(spec, "api_key")
    assert not hasattr(spec, "provider")
    assert spec.env is None


def test_prompt_never_invents_a_candidate_the_deterministic_pass_did_not_propose() -> None:
    candidates = _candidates()
    planner = ModelPlanner(_spec("fake_model_ok.py"))
    prompt = planner.build_prompt(
        server_subject=_SERVER_SUBJECT, tool_catalog=_CATALOG, deterministic_candidates=candidates, sandbox=None
    )
    import json

    context = json.loads(prompt[prompt.index("{") :])
    assert context["deterministic_candidates"] == candidates.to_json()
    assert context["server_subject"] == _SERVER_SUBJECT


def test_compare_plans_identical_plans_score_full_agreement() -> None:
    plan = ProbePlan.model_validate(
        {
            "subject_digest": _SERVER_SUBJECT,
            "cleanup_budget": 1,
            "steps": [
                {"id": "d", "primitive": "DISCOVER", "tool_subject": _SERVER_SUBJECT},
                {"id": "c", "primitive": "CALL_ONCE", "tool_subject": _SUBJECTS["make_widget"], "depends_on": ["d"]},
            ],
        }
    )
    report = compare_plans(plan, plan)
    assert isinstance(report, PlanAgreementReport)
    assert report.subject_digest_match is True
    assert report.step_multiset_jaccard == 1.0
    assert report.dependency_edge_jaccard == 1.0
    assert report.agreement_score == 1.0


def test_compare_plans_uses_tool_subject_and_primitive_not_raw_step_ids() -> None:
    """Two independent model calls have no reason to agree on step *ids* --
    agreement must be computed at the (tool_subject, primitive) level."""
    plan_a = ProbePlan.model_validate(
        {
            "subject_digest": _SERVER_SUBJECT,
            "cleanup_budget": 0,
            "steps": [
                {"id": "step-one", "primitive": "DISCOVER", "tool_subject": _SERVER_SUBJECT},
                {
                    "id": "step-two",
                    "primitive": "CALL_ONCE",
                    "tool_subject": _SUBJECTS["make_widget"],
                    "depends_on": ["step-one"],
                },
            ],
        }
    )
    plan_b = ProbePlan.model_validate(
        {
            "subject_digest": _SERVER_SUBJECT,
            "cleanup_budget": 0,
            "steps": [
                {"id": "alpha", "primitive": "DISCOVER", "tool_subject": _SERVER_SUBJECT},
                {
                    "id": "beta",
                    "primitive": "CALL_ONCE",
                    "tool_subject": _SUBJECTS["make_widget"],
                    "depends_on": ["alpha"],
                },
            ],
        }
    )
    report = compare_plans(plan_a, plan_b)
    assert report.step_multiset_jaccard == 1.0
    assert report.dependency_edge_jaccard == 1.0


def test_compare_plans_disagreement_is_reflected_in_lower_scores() -> None:
    plan_a = ProbePlan.model_validate(
        {
            "subject_digest": _SERVER_SUBJECT,
            "cleanup_budget": 0,
            "steps": [{"id": "d", "primitive": "DISCOVER", "tool_subject": _SERVER_SUBJECT}],
        }
    )
    plan_b = ProbePlan.model_validate(
        {
            "subject_digest": _SERVER_SUBJECT,
            "cleanup_budget": 0,
            "steps": [
                {"id": "d", "primitive": "DISCOVER", "tool_subject": _SERVER_SUBJECT},
                {"id": "c", "primitive": "CALL_ONCE", "tool_subject": _SUBJECTS["make_widget"], "depends_on": ["d"]},
                {"id": "r", "primitive": "REPEAT", "tool_subject": _SUBJECTS["make_widget"], "depends_on": ["c"]},
            ],
        }
    )
    report = compare_plans(plan_a, plan_b)
    assert report.step_multiset_jaccard < 1.0
    assert report.agreement_score < 1.0
    assert report.plan_a_step_count == 1
    assert report.plan_b_step_count == 3
