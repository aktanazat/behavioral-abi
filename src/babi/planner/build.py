"""Plan builder: deterministic-pass-only `ProbePlan` assembly.

Composes `schema.py` (catalog analysis), `values.py` (seeded argument
synthesis), and `dependencies.py` (candidate edges) into one `ProbePlan`
per discovered server -- and enforces IMPLEMENTATION-PLAN.md's executor
gate at the one place it can be enforced before any tool is ever called:

    Do not run repeat, interrupt, concurrent, or inverse probes without a
    working observer and disposable scope. An LLM does not waive this
    gate.

With no observer and no disposable scope declared (`PlanBuilderConstraints()`,
the all-defaults case -- exactly the Day 3-4 corpus-run situation, before
any observer exists), `build_probe_plan` emits only `DISCOVER`,
`INSTANTIATE`, and `CALL_ONCE` steps: the "first iteration emits
CALL_ONCE-only valid-call plans" bar Days 3-4 must clear.

`eligible_primitives` computes the gate for the full twelve-primitive
vocabulary generically (every primitive `plan.py` defines), so it is ready
for whichever slice adds `BOUNDARY_MUTATION_FUZZ`/`CROSS_CALL_CONSISTENCY`/
`PRECONDITION_REVOCATION` scheduling (Day 7-10 material with no settled
step-parameter contract yet). `_add_tool_steps` currently only assembles
steps for the primitives IMPLEMENTATION-PLAN.md's default pipeline names
(`DISCOVER -> INSTANTIATE -> CALL_ONCE -> READ_BACK -> [REPEAT |
RETRY_AFTER_INTERRUPT | PARALLEL_DOUBLE_FIRE] -> VERIFY_WITH_TIMING ->
PERTURB_AND_UNDO`) plus the gate itself -- inventing an unreviewed
parameter shape for the remaining three primitives here would risk
contradicting whatever the slice that actually executes them settles on.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from babi.canon import Json
from babi.manifest import SandboxManifest
from babi.plan import DependencyEdge, ProbePlan, ProbePrimitive, ProbeStep, RationaleEntry, RationaleProvenance
from babi.planner.dependencies import DependencyCandidate, DeterministicCandidates, infer_all_candidates
from babi.planner.heuristics import OperationClass
from babi.planner.schema import CatalogEntry, analyze_catalog
from babi.planner.values import DEFAULT_RESOURCE_PREFIX, TypedBindings, synthesize_arguments

# IMPLEMENTATION-PLAN.md, "### Executor": gate exactly these four primitive
# families behind a working observer + disposable scope, plus the two
# later (Day 7-10) primitives that also issue extra, non-CALL_ONCE calls.
_REQUIRES_OBSERVER: frozenset[ProbePrimitive] = frozenset(
    {
        ProbePrimitive.READ_BACK,
        ProbePrimitive.REPEAT,
        ProbePrimitive.RETRY_AFTER_INTERRUPT,
        ProbePrimitive.PARALLEL_DOUBLE_FIRE,
        ProbePrimitive.VERIFY_WITH_TIMING,
        ProbePrimitive.PERTURB_AND_UNDO,
        ProbePrimitive.CROSS_CALL_CONSISTENCY,
        ProbePrimitive.PRECONDITION_REVOCATION,
    }
)
_REQUIRES_DISPOSABLE_SCOPE: frozenset[ProbePrimitive] = frozenset(
    {
        ProbePrimitive.REPEAT,
        ProbePrimitive.RETRY_AFTER_INTERRUPT,
        ProbePrimitive.PARALLEL_DOUBLE_FIRE,
        ProbePrimitive.PERTURB_AND_UNDO,
        ProbePrimitive.BOUNDARY_MUTATION_FUZZ,
        ProbePrimitive.PRECONDITION_REVOCATION,
    }
)

_MIN_CANDIDATE_SCORE = 0.3


@dataclass(frozen=True, slots=True)
class PlanBuilderConstraints:
    """Operational facts the plan builder gates on -- never a per-tool
    fact, always about the run as a whole. `observer_kinds` names which of
    the generic local instrumented-server observers (IMPLEMENTATION-PLAN.md
    `### Observers`: filesystem/process/network/container diffing,
    stderr+exit capture) are actually wired up; `read_back_available`
    additionally covers the black-box case where the *only* oracle is a
    sibling tool call (no local instrumentation at all, e.g. a pure remote
    API target). Either kind of observer satisfies the gate.
    `disposable_scope` reflects RUBRIC.md's "Generic risk policy based on
    ... presence of a read-back oracle" allowance -- a plain caller-supplied
    fact about the sandboxed account/environment, not inferred here."""

    observer_kinds: frozenset[str] = field(default_factory=frozenset)
    read_back_available: bool = False
    disposable_scope: bool = False

    @property
    def has_working_observer(self) -> bool:
        return bool(self.observer_kinds) or self.read_back_available


def disposable_scope_from_manifest(manifest: SandboxManifest | None) -> bool:
    """A sandbox is disposable once it declares both a resource prefix and
    a teardown deadline. Both are typed manifest fields, never reflected
    attributes or tool-specific configuration."""
    if manifest is None:
        return False
    return bool(manifest.resource_prefix) and bool(manifest.teardown_deadline)


def eligible_primitives(constraints: PlanBuilderConstraints) -> frozenset[ProbePrimitive]:
    """Which primitives the current constraints permit at all, independent
    of any one tool's candidates. `build_probe_plan` additionally requires
    a concrete candidate (a read-back sibling, an inverse sibling) before
    actually scheduling `READ_BACK`/`PERTURB_AND_UNDO` for a given tool.

    A primitive is eligible iff every gate it belongs to is satisfied --
    computed directly as that conjunction, not as an ad hoc set union
    (which is easy to get subtly wrong for the primitives that require
    *only* disposable scope, with no observer requirement at all)."""
    return frozenset(
        primitive
        for primitive in ProbePrimitive
        if (primitive not in _REQUIRES_OBSERVER or constraints.has_working_observer)
        and (primitive not in _REQUIRES_DISPOSABLE_SCOPE or constraints.disposable_scope)
    )


def _short_id(tool_subject: str, index: int) -> str:
    digest_part = tool_subject.removeprefix("sha256:")[:12] if tool_subject else "unknown"
    return f"{index:03d}-{digest_part}"


def _best(candidates: Sequence[DependencyCandidate]) -> DependencyCandidate | None:
    # `candidates` is already score-descending (dependencies.py `_sorted`);
    # filtering by tool_subject preserves that relative order.
    return candidates[0] if candidates else None


def _candidate_detail(candidate: DependencyCandidate) -> str:
    return "; ".join((f"score={candidate.score:.2f}", *candidate.evidence))


def _rationale(from_step: str, to_step: str, detail: str) -> RationaleEntry:
    return RationaleEntry(
        edge=DependencyEdge(from_step=from_step, to_step=to_step),
        provenance=RationaleProvenance.SCHEMA_OVERLAP,
        detail=detail,
    )


def _record_identifier_bindings(entry: CatalogEntry, arguments: Mapping[str, Json], bindings: TypedBindings) -> None:
    """After synthesizing one tool's call arguments, record any
    identifier-shaped value under its structural binding key so a later
    tool's identifier-shaped input can deterministically reuse it --
    "earlier observed values through typed bindings" (values.py), applied
    within the planning pass itself from synthesized (not live) data."""
    root = entry.analysis.input.root
    if root is None:
        return
    for child in root.properties:
        if child.is_identifier_like and child.name in arguments:
            bindings.record(child.binding_key, arguments[child.name])


def _add_tool_steps(
    entry: CatalogEntry,
    *,
    index: int,
    candidates: DeterministicCandidates,
    eligible: frozenset[ProbePrimitive],
    resource_prefix: str,
    seed: str,
    bindings: TypedBindings,
    include_optional: bool,
    discover_step_id: str,
    steps: list[ProbeStep],
    rationale: list[RationaleEntry],
) -> dict[str, Json]:
    tool_seed = f"{seed}:{entry.tool_subject}"
    arguments = synthesize_arguments(
        entry.analysis.input,
        seed=tool_seed,
        resource_prefix=resource_prefix,
        bindings=bindings,
        include_optional=include_optional,
    )
    _record_identifier_bindings(entry, arguments, bindings)

    short = _short_id(entry.tool_subject, index)
    instantiate_id = f"instantiate-{short}"
    call_id = f"call-{short}"

    steps.append(
        ProbeStep(
            id=instantiate_id,
            primitive=ProbePrimitive.INSTANTIATE,
            tool_subject=entry.tool_subject,
            depends_on=[discover_step_id],
            parameters={"arguments": arguments},
        )
    )
    rationale.append(
        _rationale(discover_step_id, instantiate_id, "INSTANTIATE synthesizes arguments for a tool DISCOVER enumerated")
    )
    steps.append(
        ProbeStep(
            id=call_id,
            primitive=ProbePrimitive.CALL_ONCE,
            tool_subject=entry.tool_subject,
            depends_on=[instantiate_id],
            parameters={"arguments": arguments},
        )
    )
    rationale.append(_rationale(instantiate_id, call_id, "CALL_ONCE executes the arguments INSTANTIATE synthesized"))

    read_back_candidate = _best(candidates.read_back_for(entry.tool_subject, min_score=_MIN_CANDIDATE_SCORE))
    read_back_step_id: str | None = None
    if ProbePrimitive.READ_BACK in eligible and read_back_candidate is not None:
        read_back_step_id = f"read-back-{short}"
        steps.append(
            ProbeStep(
                id=read_back_step_id,
                primitive=ProbePrimitive.READ_BACK,
                tool_subject=read_back_candidate.to_tool_subject,
                depends_on=[call_id],
            )
        )
        rationale.append(_rationale(call_id, read_back_step_id, _candidate_detail(read_back_candidate)))

    verification_dependency = read_back_step_id or call_id

    if ProbePrimitive.REPEAT in eligible:
        repeat_id = f"repeat-{short}"
        steps.append(
            ProbeStep(
                id=repeat_id,
                primitive=ProbePrimitive.REPEAT,
                tool_subject=entry.tool_subject,
                depends_on=[call_id],
                parameters={"arguments": arguments, "count": 2},
            )
        )
        rationale.append(_rationale(call_id, repeat_id, "REPEAT re-runs the same synthesized call to test idempotency"))
        if read_back_step_id is not None and read_back_candidate is not None:
            follow_up_id = f"read-back-repeat-{short}"
            steps.append(
                ProbeStep(
                    id=follow_up_id,
                    primitive=ProbePrimitive.READ_BACK,
                    tool_subject=read_back_candidate.to_tool_subject,
                    depends_on=[repeat_id],
                )
            )
            rationale.append(_rationale(repeat_id, follow_up_id, _candidate_detail(read_back_candidate)))

    if ProbePrimitive.RETRY_AFTER_INTERRUPT in eligible:
        retry_id = f"retry-after-interrupt-{short}"
        steps.append(
            ProbeStep(
                id=retry_id,
                primitive=ProbePrimitive.RETRY_AFTER_INTERRUPT,
                tool_subject=entry.tool_subject,
                depends_on=[call_id],
                parameters={"arguments": arguments},
            )
        )
        rationale.append(
            _rationale(call_id, retry_id, "RETRY_AFTER_INTERRUPT re-issues the same synthesized call after a severed transport")
        )

    if ProbePrimitive.PARALLEL_DOUBLE_FIRE in eligible:
        parallel_id = f"parallel-double-fire-{short}"
        steps.append(
            ProbeStep(
                id=parallel_id,
                primitive=ProbePrimitive.PARALLEL_DOUBLE_FIRE,
                tool_subject=entry.tool_subject,
                depends_on=[call_id],
                parameters={"arguments": arguments, "count": 2},
            )
        )
        rationale.append(
            _rationale(call_id, parallel_id, "PARALLEL_DOUBLE_FIRE issues concurrent copies of the same synthesized call")
        )

    if ProbePrimitive.VERIFY_WITH_TIMING in eligible and read_back_step_id is not None and read_back_candidate is not None:
        timing_id = f"verify-with-timing-{short}"
        steps.append(
            ProbeStep(
                id=timing_id,
                primitive=ProbePrimitive.VERIFY_WITH_TIMING,
                tool_subject=read_back_candidate.to_tool_subject,
                depends_on=[verification_dependency],
            )
        )
        rationale.append(_rationale(verification_dependency, timing_id, _candidate_detail(read_back_candidate)))

    inverse_candidate = _best(candidates.inverse_for(entry.tool_subject, min_score=_MIN_CANDIDATE_SCORE))
    if ProbePrimitive.PERTURB_AND_UNDO in eligible and inverse_candidate is not None:
        perturb_id = f"perturb-and-undo-{short}"
        steps.append(
            ProbeStep(
                id=perturb_id,
                primitive=ProbePrimitive.PERTURB_AND_UNDO,
                tool_subject=inverse_candidate.to_tool_subject,
                depends_on=[verification_dependency],
            )
        )
        rationale.append(_rationale(verification_dependency, perturb_id, _candidate_detail(inverse_candidate)))

    return arguments


def build_probe_plan(
    server_subject: str,
    tool_catalog: Sequence[Mapping[str, Json]],
    tool_subjects: Mapping[str, str],
    *,
    resource_prefix: str = DEFAULT_RESOURCE_PREFIX,
    seed: str = "v0",
    constraints: PlanBuilderConstraints | None = None,
    cleanup_budget: int = 0,
    include_optional: bool = False,
) -> ProbePlan:
    """Build one deterministic-pass `ProbePlan` for a whole discovered
    catalog (`babi.collect`'s `tools_list` + `tool_subjects`, verbatim).
    Same inputs -> byte-identical plan (every synthesized value is a pure
    function of schema + seed; every step id is derived from tool_subject
    + catalog position, never from a tool name)."""
    resolved_constraints = constraints if constraints is not None else PlanBuilderConstraints()
    catalog = analyze_catalog(tool_catalog, tool_subjects)
    candidates = infer_all_candidates(catalog)
    eligible = eligible_primitives(resolved_constraints)

    bindings = TypedBindings()
    steps: list[ProbeStep] = []
    rationale: list[RationaleEntry] = []
    created_resource_values: list[str] = []

    discover_step = ProbeStep(id="discover", primitive=ProbePrimitive.DISCOVER, tool_subject=server_subject)
    steps.append(discover_step)

    # CREATE-classed tools first: their synthesized identifier-shaped
    # inputs land in `bindings` before any read/update/delete/act tool is
    # synthesized, so a later tool's identifier-shaped input deterministically
    # reuses the same value instead of an unrelated one.
    ordered = sorted(
        (entry for entry in catalog if entry.tool_subject),
        key=lambda entry: (0 if entry.analysis.operation_class is OperationClass.CREATE else 1, entry.tool_subject),
    )

    for index, entry in enumerate(ordered):
        arguments = _add_tool_steps(
            entry,
            index=index,
            candidates=candidates,
            eligible=eligible,
            resource_prefix=resource_prefix,
            seed=seed,
            bindings=bindings,
            include_optional=include_optional,
            discover_step_id=discover_step.id,
            steps=steps,
            rationale=rationale,
        )
        if entry.analysis.operation_class is OperationClass.CREATE:
            root = entry.analysis.input.root
            if root is not None:
                for child in root.properties:
                    value = arguments.get(child.name)
                    if child.is_identifier_like and isinstance(value, str):
                        created_resource_values.append(value)

    generated_resources = sorted(set(created_resource_values)) if created_resource_values else (
        [resource_prefix] if resource_prefix else []
    )

    return ProbePlan(
        subject_digest=server_subject,
        prerequisites=[],
        generated_resources=generated_resources,
        steps=steps,
        observers=sorted(resolved_constraints.observer_kinds),
        cleanup_budget=cleanup_budget,
        stop_conditions=[],
        rationale_evidence=rationale,
    )


__all__ = [
    "PlanBuilderConstraints",
    "build_probe_plan",
    "disposable_scope_from_manifest",
    "eligible_primitives",
]
