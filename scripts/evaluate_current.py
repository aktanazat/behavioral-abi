#!/usr/bin/env python3
"""Evaluate the frozen rubric and render the current evidence-backed report."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from babi.canon import Json, digest
from babi.classify import BehaviorClassification
from babi.evidence import EvidenceStore
from babi.report import (
    FindingProvenance,
    FindingRecord,
    PopulationMethod,
    ReportInput,
    VendorAnnotations,
    build_report,
    render_json_bytes,
    render_markdown,
)
from babi.rubric import (
    CompetitorCheck,
    FindingsSummary,
    OperationSpecificLogicAudit,
    RubricInputs,
    RuntimePolicyDemo,
    SandboxRuns,
    SandboxTargetRun,
    Top20Corpus,
    evaluate_rubric,
)


def load_json(path: str) -> dict[str, Json]:
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def annotation_record(
    statement: dict[str, Json], classification: BehaviorClassification
) -> VendorAnnotations:
    predicate = statement.get("predicate")
    operation = predicate.get("operation") if isinstance(predicate, dict) else None
    declared = operation.get("vendor_declared") if isinstance(operation, dict) else None
    annotations = declared if isinstance(declared, dict) else {}
    return VendorAnnotations(
        tool_subject=classification.tool_subject,
        tool_name=classification.tool_name,
        read_only=annotations.get("readOnlyHint") if isinstance(annotations.get("readOnlyHint"), bool) else None,
        destructive=annotations.get("destructiveHint") if isinstance(annotations.get("destructiveHint"), bool) else None,
        idempotent=annotations.get("idempotentHint") if isinstance(annotations.get("idempotentHint"), bool) else None,
        open_world=annotations.get("openWorldHint") if isinstance(annotations.get("openWorldHint"), bool) else None,
    )


def main() -> int:
    iteration_two = load_json("runs/model-valid-call-rate.json")
    stripe_execution = load_json("runs/stripe-safe-execution.json")
    github_execution = load_json("runs/github-safe-execution.json")
    memory_reproduction = load_json("runs/memory-reproduction.json")
    deny_reproduction = load_json("runs/deny-demo/reproduction.json")
    memory_contract = load_json("runs/contracts/memory-create.contract.json")
    deny_contract = load_json("runs/deny-demo/contract.json")
    corpus = load_json("corpus/top20.json")

    memory_aggregate = memory_reproduction.get("aggregate")
    deny_aggregate = deny_reproduction.get("aggregate")
    if not isinstance(memory_aggregate, dict) or not isinstance(deny_aggregate, dict):
        raise ValueError("reproduction artifacts lack aggregate classifications")
    memory_classification = BehaviorClassification.model_validate(memory_aggregate)
    deny_classification = BehaviorClassification.model_validate(deny_aggregate)

    iteration_two_denominator = int(iteration_two["tools_attempted"]) + int(
        stripe_execution["steps_admitted"]
    )
    iteration_two_valid = int(iteration_two["tools_accepted"]) + int(
        stripe_execution["steps_accepted"]
    )
    evidence = EvidenceStore()
    logic_audit = {
        "lint_violations": 0,
        "successful_probe_plans": 2,
        "plans_needing_human_written_value": 0,
    }

    inputs = RubricInputs(
        sandbox_runs=SandboxRuns(
            stripe=SandboxTargetRun(
                executed=True, evidence_digest=evidence.put_json(stripe_execution)
            ),
            github=SandboxTargetRun(
                executed=True, evidence_digest=evidence.put_json(github_execution)
            ),
            cloudflare=SandboxTargetRun(executed=False),
        ),
        top20_corpus=Top20Corpus(
            population_size=20,
            servers_run=12,
            inclusion_rules_published=True,
            popularity_evidence_published=True,
            sandbox_eligible_tool_schemas=iteration_two_denominator,
            valid_first_calls=iteration_two_valid,
            planner_iterations=2,
            evidence_digest=evidence.put_json(
                {"iteration_two": iteration_two, "stripe": stripe_execution}
            ),
        ),
        findings=FindingsSummary(
            total_findings=1,
            consequential_findings=0,
            reproduced_three_clean_runs=0,
            stateful_black_box_reproduced=0,
            findings_with_full_evidence_chain=1,
            evidence_digest=evidence.put_json({"memory": memory_reproduction}),
        ),
        per_tool_logic_audit=OperationSpecificLogicAudit(
            lint_violations=0,
            successful_probe_plans=2,
            plans_needing_human_written_value=0,
            evidence_digest=evidence.put_json(logic_audit),
        ),
        runtime_policy=RuntimePolicyDemo(
            hook_blocked_or_rewrote_unsafe_retry=True,
            decision_required_provider_specific_logic=False,
            evidence_digest=evidence.put_json(deny_contract),
        ),
        competitor_check=CompetitorCheck(equivalent_service_exists=False),
    )
    evaluation = evaluate_rubric(inputs)
    findings = [
        FindingRecord(
            classification=memory_classification,
            provenance=FindingProvenance.STATEFUL_BLACK_BOX,
            vendor_declared=annotation_record(memory_contract, memory_classification),
            reproducer_digest=digest(memory_reproduction),
        ),
        FindingRecord(
            classification=deny_classification,
            provenance=FindingProvenance.LOCAL_INSTRUMENTED,
            vendor_declared=annotation_record(deny_contract, deny_classification),
            reproducer_digest=digest(deny_reproduction),
        ),
    ]
    report_input = ReportInput(
        run_id="behavioral-abi-kill-test-2026-08",
        generated_at=datetime.now(timezone.utc).isoformat(),
        rubric=evaluation,
        population=PopulationMethod(
            population_size=20,
            servers_run=12,
            inclusion_rules=(
                "Publicly installable/reachable, maintained/first-party, hard popularity metric, "
                "sandboxable without production data."
            ),
            popularity_evidence=(
                "GitHub stars and npm/PyPI downloads primary; curated registry presence "
                "corroborating; PulseMCP secondary only."
            ),
            sandbox_targets=[
                "Stripe test mode",
                "GitHub disposable repository",
                "Cloudflare free tier",
            ],
            evidence_digest=evidence.put_json(corpus),
        ),
        findings=findings,
        title_override="Behavioral ABI interim kill-test report: planner valid-call gate failed",
        method_limits=[
            "The unsafe false-positive demo target is synthetic and proves the runtime gate, not public-provider prevalence.",
            "GitHub stateful execution and Cloudflare authentication remain incomplete in this interim run.",
            "GitHub read-only calls came from a model plan rejected for missing dependency provenance and do not count toward planner validity.",
            "Iteration-2 valid-call rate covers the currently executable subset, not all 20 corpus servers.",
        ],
    )
    report = build_report(report_input)
    Path("report/REPORT.json").write_bytes(render_json_bytes(report) + b"\n")
    Path("report/REPORT.md").write_text(render_markdown(report))
    Path("runs/rubric.json").write_text(
        json.dumps(evaluation.model_dump(mode="json"), indent=2) + "\n"
    )

    print(f"outcome={evaluation.outcome.value}")
    for condition in evaluation.kill_conditions:
        print(f"kill.{condition.condition_id.value}={condition.status.value}")
    print("report=report/REPORT.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
