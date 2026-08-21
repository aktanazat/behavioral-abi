"""The frozen 14-day pass/kill rubric (`RUBRIC.md`), as immutable data models
and pure evaluation logic.

This module is deliberately dumb: it consumes already-measured facts (counts,
booleans, evidence digests) and applies the exact thresholds frozen in
`RUBRIC.md`. It does not collect, probe, classify, or interpret raw evidence
itself -- that is the executor's and classifier's job. Nothing here is keyed
by a tool or operation name; every input is a rubric-level aggregate fact
about the run as a whole (sandbox coverage, corpus size, finding counts,
reproduction counts, a lint result, a policy-demo outcome, a competitor
check).

Fail-closed semantics, matching `RUBRIC.md`'s "freeze pass/kill conditions
publicly before seeing results":

- A condition whose required input is absent evaluates to `pending`, never
  to a pass. Absence of evidence is not evidence of passing.
- Any kill condition that evaluates `triggered` forces the overall outcome
  to `kill`, regardless of how many pass conditions are otherwise satisfied.
- The overall outcome is `continue` only when every pass condition is
  `satisfied` and no kill condition is `triggered`.
- Otherwise the overall outcome is `pending`.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PassConditionId(str, Enum):
    """`RUBRIC.md` "Full 14-day pass conditions", items 1-7, verbatim order."""

    THREE_SANDBOXES = "three_sandboxes"
    TOP20_CORPUS = "top20_corpus"
    CONSEQUENTIAL_AND_REPRODUCED = "consequential_and_reproduced"
    STATEFUL_BLACK_BOX = "stateful_black_box"
    NO_OPERATION_SPECIFIC_CODE = "no_operation_specific_code"
    RUNTIME_HOOK_DEMO = "runtime_hook_demo"
    FULL_EVIDENCE_CHAIN = "full_evidence_chain"


class KillConditionId(str, Enum):
    """`RUBRIC.md` "Full 14-day kill conditions", items 1-7, verbatim order."""

    HUMAN_INTERVENTION_RATE = "human_intervention_rate"
    PLANNER_VALID_CALL_RATE = "planner_valid_call_rate"
    INSUFFICIENT_CONSEQUENTIAL_FINDINGS = "insufficient_consequential_findings"
    INSUFFICIENT_REPRODUCED_FINDINGS = "insufficient_reproduced_findings"
    NON_CONSEQUENTIAL_MAJORITY = "non_consequential_majority"
    EQUIVALENT_COMPETITOR_SERVICE = "equivalent_competitor_service"
    POLICY_REQUIRES_PROVIDER_LOGIC = "policy_requires_provider_logic"


PASS_CONDITION_TEXT: dict[PassConditionId, str] = {
    PassConditionId.THREE_SANDBOXES: (
        "Probe three unrelated sandbox targets: Stripe test mode, GitHub Issues "
        "in a disposable repository, and Cloudflare's free tier."
    ),
    PassConditionId.TOP20_CORPUS: (
        "Enumerate and run the same planner against 20 publicly runnable popular "
        "MCP servers. Publish exact inclusion rules and popularity evidence."
    ),
    PassConditionId.CONSEQUENTIAL_AND_REPRODUCED: (
        "Discover at least five consequential schema-invisible behaviors. At "
        "least four reproduce in three independent clean runs."
    ),
    PassConditionId.STATEFUL_BLACK_BOX: (
        "At least three reproduced findings come from stateful black-box "
        "execution, not source review or static annotation inspection."
    ),
    PassConditionId.NO_OPERATION_SPECIFIC_CODE: (
        "No operation-specific generator, assertion, inverse mapping, fixture, "
        "or cleanup routine is added."
    ),
    PassConditionId.RUNTIME_HOOK_DEMO: (
        "One Claude Code `PreToolUse` hook consumes a verified contract and "
        "blocks or rewrites one unsafe retry before execution."
    ),
    PassConditionId.FULL_EVIDENCE_CHAIN: (
        "Every public finding links to the raw probe history, environment "
        "digest, tool-definition digest, reproducer, contract, signature, and "
        "Rekor inclusion proof."
    ),
}

KILL_CONDITION_TEXT: dict[KillConditionId, str] = {
    KillConditionId.HUMAN_INTERVENTION_RATE: (
        "More than 10% of successful probe plans need a human-written "
        "operation-specific value, dependency edge, observer, or inverse."
    ),
    KillConditionId.PLANNER_VALID_CALL_RATE: (
        "The generic planner cannot make a valid first call for at least 70% "
        "of sandbox-eligible tool schemas after two model/planner iterations."
    ),
    KillConditionId.INSUFFICIENT_CONSEQUENTIAL_FINDINGS: (
        "Fewer than five consequential hidden behaviors appear after all three "
        "sandboxes and the 20-server corpus run."
    ),
    KillConditionId.INSUFFICIENT_REPRODUCED_FINDINGS: (
        "Fewer than four findings reproduce three times from clean state."
    ),
    KillConditionId.NON_CONSEQUENTIAL_MAJORITY: (
        "Most findings are only conservative metadata errors, telemetry "
        "disclosures, schema bugs, or source-versus-wire differences rather "
        "than consequential runtime behavior."
    ),
    KillConditionId.EQUIVALENT_COMPETITOR_SERVICE: (
        "Glama or another existing service already exposes equivalent stateful "
        "semantic results and history through a public API."
    ),
    KillConditionId.POLICY_REQUIRES_PROVIDER_LOGIC: (
        "Runtime policy cannot make a concrete decision from the resulting "
        "contract without provider-specific logic."
    ),
}


class SandboxTargetRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executed: bool
    evidence_digest: str | None = None


class SandboxRuns(BaseModel):
    """Pass condition 1's three named sandboxes. A missing field means that
    sandbox has not been measured at all, distinct from `executed=False`."""

    model_config = ConfigDict(extra="forbid")

    stripe: SandboxTargetRun | None = None
    github: SandboxTargetRun | None = None
    cloudflare: SandboxTargetRun | None = None

    def evidence_digests(self) -> list[str]:
        return [
            run.evidence_digest
            for run in (self.stripe, self.github, self.cloudflare)
            if run is not None and run.evidence_digest is not None
        ]


class Top20Corpus(BaseModel):
    """The 20-server corpus run: population/inclusion facts for pass
    condition 2, and the planner's valid-first-call numerator/denominator
    for kill condition 2. One measured object because both properties are
    facts about the same corpus run."""

    model_config = ConfigDict(extra="forbid")

    population_size: int = Field(ge=0)
    servers_run: int = Field(ge=0)
    inclusion_rules_published: bool
    popularity_evidence_published: bool
    sandbox_eligible_tool_schemas: int = Field(ge=0)
    valid_first_calls: int = Field(ge=0)
    planner_iterations: int = Field(ge=0)
    evidence_digest: str | None = None

    @model_validator(mode="after")
    def _valid_calls_within_population(self) -> Top20Corpus:
        if self.valid_first_calls > self.sandbox_eligible_tool_schemas:
            raise ValueError("valid_first_calls cannot exceed sandbox_eligible_tool_schemas")
        return self

    def valid_first_call_rate(self) -> float | None:
        if self.sandbox_eligible_tool_schemas == 0:
            return None
        return self.valid_first_calls / self.sandbox_eligible_tool_schemas


class FindingsSummary(BaseModel):
    """The finding ledger: total/consequential counts, reproduction counts,
    stateful-black-box counts, and full-evidence-chain counts. Backs pass
    conditions 3, 4, 7 and kill conditions 3, 4, 5."""

    model_config = ConfigDict(extra="forbid")

    total_findings: int = Field(ge=0)
    consequential_findings: int = Field(ge=0)
    reproduced_three_clean_runs: int = Field(ge=0)
    stateful_black_box_reproduced: int = Field(ge=0)
    findings_with_full_evidence_chain: int = Field(ge=0)
    evidence_digest: str | None = None

    @model_validator(mode="after")
    def _counts_are_internally_consistent(self) -> FindingsSummary:
        if self.consequential_findings > self.total_findings:
            raise ValueError("consequential_findings cannot exceed total_findings")
        if self.reproduced_three_clean_runs > self.consequential_findings:
            raise ValueError("reproduced_three_clean_runs cannot exceed consequential_findings")
        if self.stateful_black_box_reproduced > self.reproduced_three_clean_runs:
            raise ValueError(
                "stateful_black_box_reproduced cannot exceed reproduced_three_clean_runs"
            )
        if self.findings_with_full_evidence_chain > self.total_findings:
            raise ValueError("findings_with_full_evidence_chain cannot exceed total_findings")
        return self


class OperationSpecificLogicAudit(BaseModel):
    """Pass condition 5 (zero-tolerance static lint result) and kill
    condition 1 (dynamic human-intervention rate) are different measurements
    of the same "no per-tool logic" concern, so they share one input."""

    model_config = ConfigDict(extra="forbid")

    lint_violations: int = Field(ge=0)
    successful_probe_plans: int = Field(ge=0)
    plans_needing_human_written_value: int = Field(ge=0)
    evidence_digest: str | None = None

    @model_validator(mode="after")
    def _numerator_within_denominator(self) -> OperationSpecificLogicAudit:
        if self.plans_needing_human_written_value > self.successful_probe_plans:
            raise ValueError(
                "plans_needing_human_written_value cannot exceed successful_probe_plans"
            )
        return self

    def human_intervention_rate(self) -> float | None:
        if self.successful_probe_plans == 0:
            return None
        return self.plans_needing_human_written_value / self.successful_probe_plans


class RuntimePolicyDemo(BaseModel):
    """Pass condition 6 (hook demo happened) and kill condition 7 (the
    policy needed provider-specific logic to decide) are two facts about the
    same runtime-policy demonstration."""

    model_config = ConfigDict(extra="forbid")

    hook_blocked_or_rewrote_unsafe_retry: bool
    decision_required_provider_specific_logic: bool
    evidence_digest: str | None = None


class CompetitorCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equivalent_service_exists: bool
    evidence_digest: str | None = None


class RubricInputs(BaseModel):
    """All measured facts the rubric evaluates. Every field is optional:
    an absent field means that condition's inputs have not been measured
    yet, which fails closed to `pending`, never to a pass."""

    model_config = ConfigDict(extra="forbid")

    sandbox_runs: SandboxRuns = Field(default_factory=SandboxRuns)
    top20_corpus: Top20Corpus | None = None
    findings: FindingsSummary | None = None
    per_tool_logic_audit: OperationSpecificLogicAudit | None = None
    runtime_policy: RuntimePolicyDemo | None = None
    competitor_check: CompetitorCheck | None = None


class PassConditionStatus(str, Enum):
    SATISFIED = "satisfied"
    NOT_SATISFIED = "not_satisfied"
    PENDING = "pending"


class KillConditionStatus(str, Enum):
    TRIGGERED = "triggered"
    NOT_TRIGGERED = "not_triggered"
    PENDING = "pending"


class RubricOutcome(str, Enum):
    CONTINUE = "continue"
    KILL = "kill"
    PENDING = "pending"


class PassConditionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_id: PassConditionId
    status: PassConditionStatus
    detail: str
    numerator: float | None = None
    denominator: float | None = None
    evidence_digests: list[str] = Field(default_factory=list)


class KillConditionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_id: KillConditionId
    status: KillConditionStatus
    detail: str
    numerator: float | None = None
    denominator: float | None = None
    evidence_digests: list[str] = Field(default_factory=list)


class RubricEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: RubricOutcome
    pass_conditions: list[PassConditionResult]
    kill_conditions: list[KillConditionResult]


def _digests(*values: str | None) -> list[str]:
    return [value for value in values if value is not None]


def _evaluate_three_sandboxes(inputs: RubricInputs) -> PassConditionResult:
    runs = inputs.sandbox_runs
    named = (runs.stripe, runs.github, runs.cloudflare)
    if any(run is None for run in named):
        status = PassConditionStatus.PENDING
        detail = "not every one of the three named sandboxes has been measured"
    elif all(run is not None and run.executed for run in named):
        status = PassConditionStatus.SATISFIED
        detail = "stripe, github, and cloudflare sandboxes all executed"
    else:
        status = PassConditionStatus.NOT_SATISFIED
        detail = "at least one of the three named sandboxes did not execute"
    executed_count = sum(1 for run in named if run is not None and run.executed)
    return PassConditionResult(
        condition_id=PassConditionId.THREE_SANDBOXES,
        status=status,
        detail=detail,
        numerator=executed_count,
        denominator=3,
        evidence_digests=runs.evidence_digests(),
    )


def _evaluate_top20_corpus(inputs: RubricInputs) -> PassConditionResult:
    corpus = inputs.top20_corpus
    if corpus is None:
        return PassConditionResult(
            condition_id=PassConditionId.TOP20_CORPUS,
            status=PassConditionStatus.PENDING,
            detail="top-20 corpus run has not been measured",
        )
    satisfied = (
        corpus.servers_run >= 20
        and corpus.inclusion_rules_published
        and corpus.popularity_evidence_published
    )
    if satisfied:
        detail = "20+ servers run with inclusion rules and popularity evidence published"
    else:
        detail = (
            f"{corpus.servers_run}/20 servers run, "
            f"inclusion_rules_published={corpus.inclusion_rules_published}, "
            f"popularity_evidence_published={corpus.popularity_evidence_published}"
        )
    return PassConditionResult(
        condition_id=PassConditionId.TOP20_CORPUS,
        status=PassConditionStatus.SATISFIED if satisfied else PassConditionStatus.NOT_SATISFIED,
        detail=detail,
        numerator=corpus.servers_run,
        denominator=20,
        evidence_digests=_digests(corpus.evidence_digest),
    )


def _evaluate_consequential_and_reproduced(inputs: RubricInputs) -> PassConditionResult:
    findings = inputs.findings
    if findings is None:
        return PassConditionResult(
            condition_id=PassConditionId.CONSEQUENTIAL_AND_REPRODUCED,
            status=PassConditionStatus.PENDING,
            detail="findings summary has not been measured",
        )
    satisfied = findings.consequential_findings >= 5 and findings.reproduced_three_clean_runs >= 4
    detail = (
        f"{findings.consequential_findings} consequential findings "
        f"(need >=5), {findings.reproduced_three_clean_runs} reproduced 3x clean (need >=4)"
    )
    return PassConditionResult(
        condition_id=PassConditionId.CONSEQUENTIAL_AND_REPRODUCED,
        status=PassConditionStatus.SATISFIED if satisfied else PassConditionStatus.NOT_SATISFIED,
        detail=detail,
        numerator=findings.reproduced_three_clean_runs,
        denominator=findings.consequential_findings,
        evidence_digests=_digests(findings.evidence_digest),
    )


def _evaluate_stateful_black_box(inputs: RubricInputs) -> PassConditionResult:
    findings = inputs.findings
    if findings is None:
        return PassConditionResult(
            condition_id=PassConditionId.STATEFUL_BLACK_BOX,
            status=PassConditionStatus.PENDING,
            detail="findings summary has not been measured",
        )
    satisfied = findings.stateful_black_box_reproduced >= 3
    return PassConditionResult(
        condition_id=PassConditionId.STATEFUL_BLACK_BOX,
        status=PassConditionStatus.SATISFIED if satisfied else PassConditionStatus.NOT_SATISFIED,
        detail=f"{findings.stateful_black_box_reproduced} reproduced findings from stateful black-box execution (need >=3)",
        numerator=findings.stateful_black_box_reproduced,
        denominator=3,
        evidence_digests=_digests(findings.evidence_digest),
    )


def _evaluate_no_operation_specific_code(inputs: RubricInputs) -> PassConditionResult:
    audit = inputs.per_tool_logic_audit
    if audit is None:
        return PassConditionResult(
            condition_id=PassConditionId.NO_OPERATION_SPECIFIC_CODE,
            status=PassConditionStatus.PENDING,
            detail="per-tool-logic lint has not been run",
        )
    satisfied = audit.lint_violations == 0
    return PassConditionResult(
        condition_id=PassConditionId.NO_OPERATION_SPECIFIC_CODE,
        status=PassConditionStatus.SATISFIED if satisfied else PassConditionStatus.NOT_SATISFIED,
        detail=f"{audit.lint_violations} no-per-tool-logic lint violation(s)",
        numerator=audit.lint_violations,
        denominator=0,
        evidence_digests=_digests(audit.evidence_digest),
    )


def _evaluate_runtime_hook_demo(inputs: RubricInputs) -> PassConditionResult:
    policy = inputs.runtime_policy
    if policy is None:
        return PassConditionResult(
            condition_id=PassConditionId.RUNTIME_HOOK_DEMO,
            status=PassConditionStatus.PENDING,
            detail="runtime policy demo has not been measured",
        )
    satisfied = policy.hook_blocked_or_rewrote_unsafe_retry
    return PassConditionResult(
        condition_id=PassConditionId.RUNTIME_HOOK_DEMO,
        status=PassConditionStatus.SATISFIED if satisfied else PassConditionStatus.NOT_SATISFIED,
        detail=f"hook_blocked_or_rewrote_unsafe_retry={satisfied}",
        evidence_digests=_digests(policy.evidence_digest),
    )


def _evaluate_full_evidence_chain(inputs: RubricInputs) -> PassConditionResult:
    findings = inputs.findings
    if findings is None or findings.total_findings == 0:
        return PassConditionResult(
            condition_id=PassConditionId.FULL_EVIDENCE_CHAIN,
            status=PassConditionStatus.PENDING,
            detail="no findings recorded yet to check for a full evidence chain",
            evidence_digests=_digests(findings.evidence_digest) if findings else [],
        )
    satisfied = findings.findings_with_full_evidence_chain == findings.total_findings
    return PassConditionResult(
        condition_id=PassConditionId.FULL_EVIDENCE_CHAIN,
        status=PassConditionStatus.SATISFIED if satisfied else PassConditionStatus.NOT_SATISFIED,
        detail=(
            f"{findings.findings_with_full_evidence_chain}/{findings.total_findings} "
            "public findings link the full evidence chain"
        ),
        numerator=findings.findings_with_full_evidence_chain,
        denominator=findings.total_findings,
        evidence_digests=_digests(findings.evidence_digest),
    )


def _evaluate_human_intervention_rate(inputs: RubricInputs) -> KillConditionResult:
    audit = inputs.per_tool_logic_audit
    rate = audit.human_intervention_rate() if audit is not None else None
    if audit is None or rate is None:
        return KillConditionResult(
            condition_id=KillConditionId.HUMAN_INTERVENTION_RATE,
            status=KillConditionStatus.PENDING,
            detail="per-tool-logic audit has not been measured",
        )
    triggered = rate > 0.10
    return KillConditionResult(
        condition_id=KillConditionId.HUMAN_INTERVENTION_RATE,
        status=KillConditionStatus.TRIGGERED if triggered else KillConditionStatus.NOT_TRIGGERED,
        detail=(
            f"{audit.plans_needing_human_written_value}/{audit.successful_probe_plans} "
            f"successful probe plans needed a human-written value ({rate:.1%}, kill if >10%)"
        ),
        numerator=audit.plans_needing_human_written_value,
        denominator=audit.successful_probe_plans,
        evidence_digests=_digests(audit.evidence_digest),
    )


def _evaluate_planner_valid_call_rate(inputs: RubricInputs) -> KillConditionResult:
    corpus = inputs.top20_corpus
    rate = corpus.valid_first_call_rate() if corpus is not None else None
    if corpus is None or rate is None or corpus.planner_iterations < 2:
        return KillConditionResult(
            condition_id=KillConditionId.PLANNER_VALID_CALL_RATE,
            status=KillConditionStatus.PENDING,
            detail="planner valid-first-call rate has not been measured after two iterations",
        )
    triggered = rate < 0.70
    return KillConditionResult(
        condition_id=KillConditionId.PLANNER_VALID_CALL_RATE,
        status=KillConditionStatus.TRIGGERED if triggered else KillConditionStatus.NOT_TRIGGERED,
        detail=(
            f"{corpus.valid_first_calls}/{corpus.sandbox_eligible_tool_schemas} "
            f"sandbox-eligible tool schemas got a valid first call ({rate:.1%}, kill if <70%)"
        ),
        numerator=corpus.valid_first_calls,
        denominator=corpus.sandbox_eligible_tool_schemas,
        evidence_digests=_digests(corpus.evidence_digest),
    )


def _full_probe_coverage(inputs: RubricInputs) -> bool:
    runs = (inputs.sandbox_runs.stripe, inputs.sandbox_runs.github, inputs.sandbox_runs.cloudflare)
    corpus = inputs.top20_corpus
    return (
        all(run is not None and run.executed for run in runs)
        and corpus is not None
        and corpus.servers_run >= 20
    )


def _evaluate_insufficient_consequential_findings(inputs: RubricInputs) -> KillConditionResult:
    findings = inputs.findings
    if findings is None or not _full_probe_coverage(inputs):
        return KillConditionResult(
            condition_id=KillConditionId.INSUFFICIENT_CONSEQUENTIAL_FINDINGS,
            status=KillConditionStatus.PENDING,
            detail="finding-count kill gate waits for all three sandboxes and the 20-server corpus",
        )
    triggered = findings.consequential_findings < 5
    return KillConditionResult(
        condition_id=KillConditionId.INSUFFICIENT_CONSEQUENTIAL_FINDINGS,
        status=KillConditionStatus.TRIGGERED if triggered else KillConditionStatus.NOT_TRIGGERED,
        detail=f"{findings.consequential_findings} consequential findings (kill if <5)",
        numerator=findings.consequential_findings,
        denominator=5,
        evidence_digests=_digests(findings.evidence_digest),
    )


def _evaluate_insufficient_reproduced_findings(inputs: RubricInputs) -> KillConditionResult:
    findings = inputs.findings
    if findings is None or not _full_probe_coverage(inputs):
        return KillConditionResult(
            condition_id=KillConditionId.INSUFFICIENT_REPRODUCED_FINDINGS,
            status=KillConditionStatus.PENDING,
            detail="reproduction-count kill gate waits for all three sandboxes and the 20-server corpus",
        )
    triggered = findings.reproduced_three_clean_runs < 4
    return KillConditionResult(
        condition_id=KillConditionId.INSUFFICIENT_REPRODUCED_FINDINGS,
        status=KillConditionStatus.TRIGGERED if triggered else KillConditionStatus.NOT_TRIGGERED,
        detail=f"{findings.reproduced_three_clean_runs} findings reproduced 3x clean (kill if <4)",
        numerator=findings.reproduced_three_clean_runs,
        denominator=4,
        evidence_digests=_digests(findings.evidence_digest),
    )


def _evaluate_non_consequential_majority(inputs: RubricInputs) -> KillConditionResult:
    findings = inputs.findings
    if findings is None or findings.total_findings == 0 or not _full_probe_coverage(inputs):
        return KillConditionResult(
            condition_id=KillConditionId.NON_CONSEQUENTIAL_MAJORITY,
            status=KillConditionStatus.PENDING,
            detail="non-consequential-majority gate waits for findings and full probe coverage",
            evidence_digests=_digests(findings.evidence_digest) if findings else [],
        )
    non_consequential = findings.total_findings - findings.consequential_findings
    triggered = non_consequential > findings.consequential_findings
    return KillConditionResult(
        condition_id=KillConditionId.NON_CONSEQUENTIAL_MAJORITY,
        status=KillConditionStatus.TRIGGERED if triggered else KillConditionStatus.NOT_TRIGGERED,
        detail=(
            f"{non_consequential}/{findings.total_findings} findings are non-consequential "
            f"(kill if a majority)"
        ),
        numerator=non_consequential,
        denominator=findings.total_findings,
        evidence_digests=_digests(findings.evidence_digest),
    )


def _evaluate_equivalent_competitor_service(inputs: RubricInputs) -> KillConditionResult:
    check = inputs.competitor_check
    if check is None:
        return KillConditionResult(
            condition_id=KillConditionId.EQUIVALENT_COMPETITOR_SERVICE,
            status=KillConditionStatus.PENDING,
            detail="competitor check has not been measured",
        )
    triggered = check.equivalent_service_exists
    return KillConditionResult(
        condition_id=KillConditionId.EQUIVALENT_COMPETITOR_SERVICE,
        status=KillConditionStatus.TRIGGERED if triggered else KillConditionStatus.NOT_TRIGGERED,
        detail=f"equivalent_service_exists={triggered}",
        evidence_digests=_digests(check.evidence_digest),
    )


def _evaluate_policy_requires_provider_logic(inputs: RubricInputs) -> KillConditionResult:
    policy = inputs.runtime_policy
    if policy is None:
        return KillConditionResult(
            condition_id=KillConditionId.POLICY_REQUIRES_PROVIDER_LOGIC,
            status=KillConditionStatus.PENDING,
            detail="runtime policy demo has not been measured",
        )
    triggered = policy.decision_required_provider_specific_logic
    return KillConditionResult(
        condition_id=KillConditionId.POLICY_REQUIRES_PROVIDER_LOGIC,
        status=KillConditionStatus.TRIGGERED if triggered else KillConditionStatus.NOT_TRIGGERED,
        detail=f"decision_required_provider_specific_logic={triggered}",
        evidence_digests=_digests(policy.evidence_digest),
    )


_PASS_EVALUATORS = (
    _evaluate_three_sandboxes,
    _evaluate_top20_corpus,
    _evaluate_consequential_and_reproduced,
    _evaluate_stateful_black_box,
    _evaluate_no_operation_specific_code,
    _evaluate_runtime_hook_demo,
    _evaluate_full_evidence_chain,
)

_KILL_EVALUATORS = (
    _evaluate_human_intervention_rate,
    _evaluate_planner_valid_call_rate,
    _evaluate_insufficient_consequential_findings,
    _evaluate_insufficient_reproduced_findings,
    _evaluate_non_consequential_majority,
    _evaluate_equivalent_competitor_service,
    _evaluate_policy_requires_provider_logic,
)


def evaluate_rubric(inputs: RubricInputs) -> RubricEvaluation:
    """Pure, deterministic evaluation of every frozen pass/kill condition.

    Fails closed: any kill condition triggering forces `kill`; otherwise
    `continue` requires every pass condition to be `satisfied`; anything
    else -- missing data or an unsatisfied-but-not-killing pass condition --
    is `pending`.
    """
    pass_results = [evaluator(inputs) for evaluator in _PASS_EVALUATORS]
    kill_results = [evaluator(inputs) for evaluator in _KILL_EVALUATORS]

    any_killed = any(result.status is KillConditionStatus.TRIGGERED for result in kill_results)
    all_passed = all(result.status is PassConditionStatus.SATISFIED for result in pass_results)

    if any_killed:
        outcome = RubricOutcome.KILL
    elif all_passed:
        outcome = RubricOutcome.CONTINUE
    else:
        outcome = RubricOutcome.PENDING

    return RubricEvaluation(outcome=outcome, pass_conditions=pass_results, kill_conditions=kill_results)
