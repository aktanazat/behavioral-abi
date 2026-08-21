import pytest
from pydantic import ValidationError

from babi.rubric import (
    CompetitorCheck,
    FindingsSummary,
    KillConditionId,
    KillConditionStatus,
    OperationSpecificLogicAudit,
    PassConditionId,
    PassConditionStatus,
    RubricInputs,
    RubricOutcome,
    RuntimePolicyDemo,
    SandboxRuns,
    SandboxTargetRun,
    Top20Corpus,
    evaluate_rubric,
)


def _full_pass_inputs(**overrides: object) -> RubricInputs:
    """A fixture where every pass condition is satisfied and no kill
    condition is triggered."""
    base: dict[str, object] = {
        "sandbox_runs": SandboxRuns(
            stripe=SandboxTargetRun(executed=True, evidence_digest="sha256:" + "1" * 64),
            github=SandboxTargetRun(executed=True, evidence_digest="sha256:" + "2" * 64),
            cloudflare=SandboxTargetRun(executed=True, evidence_digest="sha256:" + "3" * 64),
        ),
        "top20_corpus": Top20Corpus(
            population_size=20,
            servers_run=20,
            inclusion_rules_published=True,
            popularity_evidence_published=True,
            sandbox_eligible_tool_schemas=100,
            valid_first_calls=80,
            planner_iterations=2,
            evidence_digest="sha256:" + "4" * 64,
        ),
        "findings": FindingsSummary(
            total_findings=6,
            consequential_findings=5,
            reproduced_three_clean_runs=4,
            stateful_black_box_reproduced=3,
            findings_with_full_evidence_chain=6,
            evidence_digest="sha256:" + "5" * 64,
        ),
        "per_tool_logic_audit": OperationSpecificLogicAudit(
            lint_violations=0,
            successful_probe_plans=50,
            plans_needing_human_written_value=2,
            evidence_digest="sha256:" + "6" * 64,
        ),
        "runtime_policy": RuntimePolicyDemo(
            hook_blocked_or_rewrote_unsafe_retry=True,
            decision_required_provider_specific_logic=False,
            evidence_digest="sha256:" + "7" * 64,
        ),
        "competitor_check": CompetitorCheck(
            equivalent_service_exists=False,
            evidence_digest="sha256:" + "8" * 64,
        ),
    }
    base.update(overrides)
    return RubricInputs.model_validate(base)


def test_all_conditions_satisfied_yields_continue() -> None:
    evaluation = evaluate_rubric(_full_pass_inputs())
    assert evaluation.outcome is RubricOutcome.CONTINUE
    assert {r.condition_id for r in evaluation.pass_conditions} == set(PassConditionId)
    assert all(r.status is PassConditionStatus.SATISFIED for r in evaluation.pass_conditions)
    assert {r.condition_id for r in evaluation.kill_conditions} == set(KillConditionId)
    assert all(r.status is KillConditionStatus.NOT_TRIGGERED for r in evaluation.kill_conditions)


def test_empty_inputs_are_all_pending() -> None:
    evaluation = evaluate_rubric(RubricInputs())
    assert evaluation.outcome is RubricOutcome.PENDING
    assert all(r.status is PassConditionStatus.PENDING for r in evaluation.pass_conditions)
    assert all(r.status is KillConditionStatus.PENDING for r in evaluation.kill_conditions)


def test_pending_metric_never_counts_as_pass() -> None:
    # Only sandboxes measured; everything else missing.
    inputs = RubricInputs(
        sandbox_runs=SandboxRuns(
            stripe=SandboxTargetRun(executed=True),
            github=SandboxTargetRun(executed=True),
            cloudflare=SandboxTargetRun(executed=True),
        )
    )
    evaluation = evaluate_rubric(inputs)
    assert evaluation.outcome is RubricOutcome.PENDING
    by_id = {r.condition_id: r for r in evaluation.pass_conditions}
    assert by_id[PassConditionId.THREE_SANDBOXES].status is PassConditionStatus.SATISFIED
    assert by_id[PassConditionId.TOP20_CORPUS].status is PassConditionStatus.PENDING


def test_finding_kill_gates_wait_for_full_probe_coverage() -> None:
    inputs = RubricInputs(
        sandbox_runs=SandboxRuns(
            stripe=SandboxTargetRun(executed=True),
            github=SandboxTargetRun(executed=True),
            cloudflare=SandboxTargetRun(executed=False),
        ),
        top20_corpus=Top20Corpus(
            population_size=20,
            servers_run=10,
            inclusion_rules_published=True,
            popularity_evidence_published=True,
            sandbox_eligible_tool_schemas=61,
            valid_first_calls=15,
            planner_iterations=1,
        ),
        findings=FindingsSummary(
            total_findings=2,
            consequential_findings=0,
            reproduced_three_clean_runs=0,
            stateful_black_box_reproduced=0,
            findings_with_full_evidence_chain=1,
        ),
    )
    evaluation = evaluate_rubric(inputs)
    by_id = {result.condition_id: result for result in evaluation.kill_conditions}
    assert by_id[KillConditionId.INSUFFICIENT_CONSEQUENTIAL_FINDINGS].status is KillConditionStatus.PENDING
    assert by_id[KillConditionId.INSUFFICIENT_REPRODUCED_FINDINGS].status is KillConditionStatus.PENDING
    assert by_id[KillConditionId.NON_CONSEQUENTIAL_MAJORITY].status is KillConditionStatus.PENDING

def test_unsatisfied_pass_condition_without_kill_is_pending_not_kill() -> None:
    inputs = _full_pass_inputs(
        top20_corpus=Top20Corpus(
            population_size=20,
            servers_run=12,
            inclusion_rules_published=True,
            popularity_evidence_published=True,
            sandbox_eligible_tool_schemas=100,
            valid_first_calls=80,
            planner_iterations=2,
        )
    )
    evaluation = evaluate_rubric(inputs)
    assert evaluation.outcome is RubricOutcome.PENDING
    by_id = {r.condition_id: r for r in evaluation.pass_conditions}
    assert by_id[PassConditionId.TOP20_CORPUS].status is PassConditionStatus.NOT_SATISFIED
    assert by_id[PassConditionId.TOP20_CORPUS].numerator == 12
    assert by_id[PassConditionId.TOP20_CORPUS].denominator == 20


@pytest.mark.parametrize(
    "condition_id,overrides",
    [
        (
            KillConditionId.HUMAN_INTERVENTION_RATE,
            {
                "per_tool_logic_audit": OperationSpecificLogicAudit(
                    lint_violations=0,
                    successful_probe_plans=50,
                    plans_needing_human_written_value=6,  # 12% > 10%
                )
            },
        ),
        (
            KillConditionId.PLANNER_VALID_CALL_RATE,
            {
                "top20_corpus": Top20Corpus(
                    population_size=20,
                    servers_run=20,
                    inclusion_rules_published=True,
                    popularity_evidence_published=True,
                    sandbox_eligible_tool_schemas=100,
                    valid_first_calls=50,  # 50% < 70%
                    planner_iterations=2,
                )
            },
        ),
        (
            KillConditionId.INSUFFICIENT_CONSEQUENTIAL_FINDINGS,
            {
                "findings": FindingsSummary(
                    total_findings=6,
                    consequential_findings=4,  # < 5
                    reproduced_three_clean_runs=4,
                    stateful_black_box_reproduced=3,
                    findings_with_full_evidence_chain=6,
                )
            },
        ),
        (
            KillConditionId.INSUFFICIENT_REPRODUCED_FINDINGS,
            {
                "findings": FindingsSummary(
                    total_findings=6,
                    consequential_findings=5,
                    reproduced_three_clean_runs=3,  # < 4
                    stateful_black_box_reproduced=3,
                    findings_with_full_evidence_chain=6,
                )
            },
        ),
        (
            KillConditionId.NON_CONSEQUENTIAL_MAJORITY,
            {
                "findings": FindingsSummary(
                    total_findings=11,
                    consequential_findings=5,  # 6 non-consequential > 5 consequential
                    reproduced_three_clean_runs=4,
                    stateful_black_box_reproduced=3,
                    findings_with_full_evidence_chain=11,
                )
            },
        ),
        (
            KillConditionId.EQUIVALENT_COMPETITOR_SERVICE,
            {"competitor_check": CompetitorCheck(equivalent_service_exists=True)},
        ),
        (
            KillConditionId.POLICY_REQUIRES_PROVIDER_LOGIC,
            {
                "runtime_policy": RuntimePolicyDemo(
                    hook_blocked_or_rewrote_unsafe_retry=True,
                    decision_required_provider_specific_logic=True,
                )
            },
        ),
    ],
)
def test_each_kill_condition_forces_kill_outcome(
    condition_id: KillConditionId, overrides: dict[str, object]
) -> None:
    evaluation = evaluate_rubric(_full_pass_inputs(**overrides))
    assert evaluation.outcome is RubricOutcome.KILL
    by_id = {r.condition_id: r for r in evaluation.kill_conditions}
    assert by_id[condition_id].status is KillConditionStatus.TRIGGERED


def test_conflicting_metrics_kill_wins_over_all_satisfied_pass_conditions() -> None:
    """Every pass condition can be independently satisfied while a kill
    condition on unrelated data is triggered; kill must win."""
    inputs = _full_pass_inputs(
        competitor_check=CompetitorCheck(equivalent_service_exists=True),
    )
    evaluation = evaluate_rubric(inputs)
    assert all(r.status is PassConditionStatus.SATISFIED for r in evaluation.pass_conditions)
    assert evaluation.outcome is RubricOutcome.KILL
    by_id = {r.condition_id: r for r in evaluation.kill_conditions}
    assert by_id[KillConditionId.EQUIVALENT_COMPETITOR_SERVICE].status is KillConditionStatus.TRIGGERED


def test_conflicting_metrics_kill_wins_via_shared_runtime_policy_object() -> None:
    """hook demo (pass 6) and provider-logic requirement (kill 7) are two
    facts about the same object and can disagree."""
    inputs = _full_pass_inputs(
        runtime_policy=RuntimePolicyDemo(
            hook_blocked_or_rewrote_unsafe_retry=True,
            decision_required_provider_specific_logic=True,
        )
    )
    evaluation = evaluate_rubric(inputs)
    by_pass_id = {r.condition_id: r for r in evaluation.pass_conditions}
    assert by_pass_id[PassConditionId.RUNTIME_HOOK_DEMO].status is PassConditionStatus.SATISFIED
    assert evaluation.outcome is RubricOutcome.KILL


def test_zero_findings_full_evidence_chain_is_pending_not_vacuously_satisfied() -> None:
    inputs = _full_pass_inputs(
        findings=FindingsSummary(
            total_findings=0,
            consequential_findings=0,
            reproduced_three_clean_runs=0,
            stateful_black_box_reproduced=0,
            findings_with_full_evidence_chain=0,
        )
    )
    evaluation = evaluate_rubric(inputs)
    by_id = {r.condition_id: r for r in evaluation.pass_conditions}
    assert by_id[PassConditionId.FULL_EVIDENCE_CHAIN].status is PassConditionStatus.PENDING


def test_findings_summary_rejects_inconsistent_counts() -> None:
    with pytest.raises(ValidationError):
        FindingsSummary(
            total_findings=1,
            consequential_findings=5,
            reproduced_three_clean_runs=0,
            stateful_black_box_reproduced=0,
            findings_with_full_evidence_chain=0,
        )


def test_top20_corpus_rejects_valid_calls_exceeding_population() -> None:
    with pytest.raises(ValidationError):
        Top20Corpus(
            population_size=20,
            servers_run=20,
            inclusion_rules_published=True,
            popularity_evidence_published=True,
            sandbox_eligible_tool_schemas=10,
            valid_first_calls=11,
            planner_iterations=2,
        )


def test_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        RubricInputs.model_validate({"unexpected_field": "nope"})
