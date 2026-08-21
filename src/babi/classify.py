"""Deterministic classifier: pure functions from a `ProbeHistory` (or several,
for cross-run reproduction) to `PropertyClaim`s and `DiscrepancyClass`es.

No I/O, no wall-clock, no randomness, and no model call lives here -- every
function is a pure predicate over already-recorded history/observer data and
the target's own discovered tool definition. A model may explain a
classification after the fact; nothing here lets one set it (IMPLEMENTATION-
PLAN.md, "Classifier": "The model may explain a result after classification.
It cannot set the classification.").

Two deterministic predicates anchor everything else:

- Identical repeated *intended* effect (as read back through the one
  `read_back`-kind observer bound to a step's own planner-inferred
  dependency edge) is an idempotency claim; ancillary evidence -- any other
  observer kind, e.g. `filesystem_diff` or a standalone `count_digest` not
  tied to that binding -- never enters the predicate, so log/telemetry
  growth alone cannot flip it (RFC 9110 SS9.2.2's ancillary-logging carve-out).
- No `read_back` evidence at all for a step -> `inconclusive`, never
  inferred from schema, annotation, or absence.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import Enum
from itertools import groupby

from pydantic import BaseModel, ConfigDict, Field

from babi.canon import Json
from babi.history import HistoryEvent, ObservationStatus, ObserverResult, ProbeHistory, history_digest
from babi.plan import ProbePrimitive

# `PropertyClaim.status` and `ObserverResult.status` are the same four-state
# vocabulary, deliberately: a property claim IS an observation, just one
# already reduced across a step's events instead of reported by one
# observer. Aliased rather than redefined so the two are interchangeable,
# never accidentally-incompatible near-duplicates.
PropertyStatus = ObservationStatus

# The one observer kind this module treats as evidence about a step's own
# *intended* effect (bound via depends_on + schema-overlap in `babi.execute`
# to the planner's own dependency edge). Every other kind -- including a
# standalone `count_digest` not produced through that binding -- is
# ancillary by construction and never consulted below.
_EFFECT_OBSERVER_KIND = "read_back"

# Emitted by the executor for every RETRY_AFTER_INTERRUPT / PARALLEL_DOUBLE_FIRE
# attempt that tried to interrupt the transport, whether or not it actually fired.
_TRANSPORT_INTERRUPT_KIND = "transport_interrupt"

_INTERRUPT_PRIMITIVES = frozenset({ProbePrimitive.RETRY_AFTER_INTERRUPT})
_REPRODUCIBLE_PRIMITIVES = frozenset(
    {ProbePrimitive.REPEAT, ProbePrimitive.RETRY_AFTER_INTERRUPT, ProbePrimitive.PARALLEL_DOUBLE_FIRE}
)


class DiscrepancyClass(str, Enum):
    """The seven claimed-vs-observed discrepancy classes (IMPLEMENTATION-
    PLAN.md, "Classifier"). Membership here is closed -- an eighth class is
    a rubric change, not a code change."""

    unsafe_false_positive = "unsafe_false_positive"
    conservative_false_negative = "conservative_false_negative"
    deployment_variance = "deployment_variance"
    unobservable_claim = "unobservable_claim"
    undeclared_side_effect = "undeclared_side_effect"
    consistency_drift = "consistency_drift"
    wire_source_drift = "wire_source_drift"


class PropertyClaim(BaseModel):
    """One classified property, scoped to exactly the preconditions it was
    observed under (`scope`, e.g. a primitive name like `"REPEAT"`) --
    never a bare unscoped `idempotent: true` (IMPLEMENTATION-PLAN.md,
    "Classifier": "Include an evidence scope")."""

    model_config = ConfigDict(extra="forbid")

    property: str
    status: PropertyStatus
    runs: int = Field(ge=1)
    evidence_digests: list[str] = Field(default_factory=list)
    scope: str
    detail: str | None = None


class BehaviorClassification(BaseModel):
    """The classifier's full verdict for one tool, addressed by `tool_subject`
    -- never by `tool_name` alone -- so a contract never silently reattaches
    to a same-named tool on a different server or schema."""

    model_config = ConfigDict(extra="forbid")

    tool_subject: str
    tool_name: str
    claims: list[PropertyClaim] = Field(default_factory=list)
    discrepancy_classes: list[DiscrepancyClass] = Field(default_factory=list)
    history_digests: list[str] = Field(default_factory=list)


def _observer_results(events: Iterable[HistoryEvent], kind: str) -> list[ObserverResult]:
    return [result for event in events for result in event.observer_results if result.kind == kind]


def _evidence_digests(results: Iterable[ObserverResult]) -> list[str]:
    seen: dict[str, None] = {}
    for result in results:
        if result.evidence_digest is not None:
            seen.setdefault(result.evidence_digest, None)
    return list(seen)


def _effect_reading(result: ObserverResult) -> Mapping[str, Json] | None:
    """A `read_back` result's `value` as a mapping, if it is determinate.

    `inconclusive`/`not_applicable` readings (the read-back call itself
    failed, or found nothing to bind) contribute no comparable value."""
    if result.status not in (ObservationStatus.observed_true, ObservationStatus.observed_false):
        return None
    return result.value if isinstance(result.value, dict) else None


def _effect_equal(a: Mapping[str, Json], b: Mapping[str, Json]) -> bool:
    """Same intended effect, per the most specific signal both readings
    share: a resource *count* (did a repeat call add another one) when both
    have it, else exact content digest. Never compares raw response bodies
    directly -- those can carry ancillary noise (timestamps, request ids)
    a count/digest summary has already normalized away."""
    count_a, count_b = a.get("count"), b.get("count")
    if count_a is not None and count_b is not None:
        return count_a == count_b
    return a.get("digest") == b.get("digest")


def _interrupt_took_hold(events: Sequence[HistoryEvent]) -> bool | None:
    """Whether the transport-level interrupt this step attempted actually
    fired at least once, per the executor's own `transport_interrupt`
    observer results.

    Returns `None` when the step never attempted an interrupt at all (no
    such observer result exists) -- distinct from `False`, which means an
    interrupt was attempted every time but the MCP SDK's own timeout/cancel
    never won the race against the response, i.e. genuinely unsupported/
    unobservable for this run, never inferred either way.
    """
    results = _observer_results(events, _TRANSPORT_INTERRUPT_KIND)
    if not results:
        return None
    return any(result.status == ObservationStatus.observed_true for result in results)


def _idempotency_claim(step_id: str, primitive: ProbePrimitive, events: Sequence[HistoryEvent]) -> PropertyClaim:
    scope = primitive.value
    effect_results = _observer_results(events, _EFFECT_OBSERVER_KIND)
    evidence_digests = _evidence_digests(effect_results)

    if primitive in _INTERRUPT_PRIMITIVES:
        took_hold = _interrupt_took_hold(events)
        if took_hold is False:
            return PropertyClaim(
                property="idempotent",
                status=PropertyStatus.inconclusive,
                runs=len(events),
                evidence_digests=_evidence_digests(_observer_results(events, _TRANSPORT_INTERRUPT_KIND)),
                scope=scope,
                detail=(
                    "transport-level interruption was attempted but never observed to take hold "
                    "(the response always arrived before the interrupt budget elapsed); "
                    "retry-after-interrupt semantics were not exercised for this run"
                ),
            )

    if not effect_results:
        return PropertyClaim(
            property="idempotent",
            status=PropertyStatus.inconclusive,
            runs=len(events),
            evidence_digests=[],
            scope=scope,
            detail=f"no {_EFFECT_OBSERVER_KIND!r} observer result recorded for step {step_id!r}",
        )

    readings = [reading for result in effect_results if (reading := _effect_reading(result)) is not None]
    if not readings:
        return PropertyClaim(
            property="idempotent",
            status=PropertyStatus.inconclusive,
            runs=len(events),
            evidence_digests=evidence_digests,
            scope=scope,
            detail=f"every {_EFFECT_OBSERVER_KIND!r} observer result was inconclusive for step {step_id!r}",
        )

    baseline = readings[0]
    consistent = all(_effect_equal(baseline, reading) for reading in readings[1:])
    return PropertyClaim(
        property="idempotent",
        status=PropertyStatus.observed_true if consistent else PropertyStatus.observed_false,
        runs=len(readings),
        evidence_digests=evidence_digests,
        scope=scope,
        detail=None if consistent else "the intended-effect reading changed across repeated identical calls",
    )


def classify_idempotency(history: ProbeHistory, tool_subject: str) -> list[PropertyClaim]:
    """One idempotency `PropertyClaim` per step that ran this tool under a
    repeat-shaped primitive (`REPEAT`, `RETRY_AFTER_INTERRUPT`,
    `PARALLEL_DOUBLE_FIRE`) and produced more than one event -- a single
    `CALL_ONCE` has nothing to compare against, so it contributes no claim."""
    claims: list[PropertyClaim] = []
    for step_id, events in _events_by_step(history, tool_subject).items():
        if len(events) < 2:
            continue
        primitive = events[0].primitive
        if primitive not in _REPRODUCIBLE_PRIMITIVES:
            continue
        claims.append(_idempotency_claim(step_id, primitive, events))
    return claims


def _events_by_step(history: ProbeHistory, tool_subject: str) -> dict[str, list[HistoryEvent]]:
    matching = [event for event in history.events if event.tool_subject == tool_subject]
    matching.sort(key=lambda event: event.sequence)
    grouped: dict[str, list[HistoryEvent]] = {}
    for step_id, group in groupby(matching, key=lambda event: event.step_id):
        grouped.setdefault(step_id, []).extend(group)
    return grouped


def _vendor_annotations(tool_definition: Mapping[str, Json] | None) -> Mapping[str, Json]:
    if tool_definition is None:
        return {}
    annotations = tool_definition.get("annotations")
    return annotations if isinstance(annotations, dict) else {}


def classify_discrepancies(
    tool_definition: Mapping[str, Json] | None,
    claims: Sequence[PropertyClaim],
) -> list[DiscrepancyClass]:
    """Map vendor-declared annotations against observed `claims` to the
    closed set of discrepancy classes. Only the two classes decidable from a
    single tool's own annotations-vs-observation pair are computed here;
    `deployment_variance`/`wire_source_drift`/`consistency_drift` need a
    cross-run or cross-subject comparison (`compare_tool_definitions`,
    `classify_consistency_drift` below) and `unobservable_claim` needs a
    claim actually marked `inconclusive` while the vendor claims a property
    -- both handled generically over the same `claims` sequence here."""
    annotations = _vendor_annotations(tool_definition)
    declared_idempotent = annotations.get("idempotentHint")
    declared_read_only = annotations.get("readOnlyHint")

    classes: list[DiscrepancyClass] = []
    for claim in claims:
        if claim.property != "idempotent":
            continue
        vendor_claims_safe = declared_idempotent is True or declared_read_only is True
        vendor_denies_safe = declared_idempotent is False

        if vendor_claims_safe and claim.status is PropertyStatus.observed_false:
            classes.append(DiscrepancyClass.unsafe_false_positive)
        elif vendor_denies_safe and claim.status is PropertyStatus.observed_true:
            classes.append(DiscrepancyClass.conservative_false_negative)
        elif vendor_claims_safe and claim.status is PropertyStatus.inconclusive:
            classes.append(DiscrepancyClass.unobservable_claim)

    return _dedupe(classes)


def classify_undeclared_side_effects(
    tool_definition: Mapping[str, Json] | None,
    events: Iterable[HistoryEvent],
) -> list[DiscrepancyClass]:
    """`undeclared_side_effect`: a `readOnlyHint: true` (or absent, since the
    spec default is `false` but a tool making no safety claim at all still
    invites the same check) tool for which a local environment observer
    (`filesystem_diff`, `process_snapshot`, `container_diff`, `network`) --
    always ancillary for idempotency, but exactly the intended signal here
    -- reported `observed_true`, i.e. a concrete effect outside the
    declared read-only surface."""
    annotations = _vendor_annotations(tool_definition)
    if annotations.get("readOnlyHint") is not True:
        return []

    local_kinds = {"filesystem_diff", "process_snapshot", "container_diff", "network"}
    for event in events:
        for result in event.observer_results:
            if result.kind in local_kinds and result.status is ObservationStatus.observed_true:
                return [DiscrepancyClass.undeclared_side_effect]
    return []


def compare_tool_definitions(
    current: Mapping[str, Json] | None,
    previous: Mapping[str, Json] | None,
) -> list[DiscrepancyClass]:
    """Cross-capture drift for what the classifier can already tell is "the
    same" tool by shape (identical name, input schema, and output schema --
    never by subject digest, since subject digests differ by construction
    whenever the server/environment differs, which is exactly the case this
    checks): `deployment_variance` when the schemas match but the
    annotations differ; `wire_source_drift` when the annotations match byte
    for byte but the raw tool definition (serialization) differs elsewhere.
    """
    if current is None or previous is None:
        return []
    if current.get("name") != previous.get("name"):
        return []
    if current.get("inputSchema") != previous.get("inputSchema"):
        return []
    if current.get("outputSchema") != previous.get("outputSchema"):
        return []

    current_annotations = _vendor_annotations(current)
    previous_annotations = _vendor_annotations(previous)
    if current_annotations != previous_annotations:
        return [DiscrepancyClass.deployment_variance]
    if current != previous:
        return [DiscrepancyClass.wire_source_drift]
    return []


def classify_consistency_drift(
    current_samples_ms: Sequence[float],
    baseline_samples_ms: Sequence[float],
    *,
    factor: float = 3.0,
) -> list[DiscrepancyClass]:
    """`consistency_drift`: a `VERIFY_WITH_TIMING` visibility-latency sample
    set whose median moved by more than `factor`x the prior baseline's
    median. Needs at least one sample on each side; fewer is
    `inconclusive` by omission (no claim emitted), never inferred as
    drift or stability from a single side alone."""
    if not current_samples_ms or not baseline_samples_ms:
        return []
    current_median = _median(current_samples_ms)
    baseline_median = _median(baseline_samples_ms)
    if baseline_median <= 0:
        return []
    if current_median > baseline_median * factor or current_median < baseline_median / factor:
        return [DiscrepancyClass.consistency_drift]
    return []


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _dedupe(classes: Iterable[DiscrepancyClass]) -> list[DiscrepancyClass]:
    seen: dict[DiscrepancyClass, None] = {}
    for cls in classes:
        seen.setdefault(cls, None)
    return list(seen)


def classify_history(
    history: ProbeHistory,
    tool_subject: str,
    tool_name: str,
    *,
    tool_definition: Mapping[str, Json] | None = None,
) -> BehaviorClassification:
    """The full per-run classification for one tool: every idempotency claim
    this history supports, plus every discrepancy class decidable from this
    single history and its tool's own vendor-declared annotations."""
    claims = classify_idempotency(history, tool_subject)
    events = [event for event in history.events if event.tool_subject == tool_subject]

    discrepancies = classify_discrepancies(tool_definition, claims)
    discrepancies += classify_undeclared_side_effects(tool_definition, events)

    return BehaviorClassification(
        tool_subject=tool_subject,
        tool_name=tool_name,
        claims=claims,
        discrepancy_classes=_dedupe(discrepancies),
        history_digests=[history_digest(history)],
    )


def _dedupe_values(values: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)


def aggregate_reproductions(classifications: Sequence[BehaviorClassification]) -> BehaviorClassification:
    """Merge classifications for the *same* tool from independent clean-state
    runs (RUBRIC.md pass condition 3: "At least four reproduce in three
    independent clean runs") into one combined verdict.

    Per property+scope: if every run that produced a determinate
    (`observed_true`/`observed_false`) reading agrees, the merged status is
    that agreement with `runs` = how many runs support it; any disagreement
    between two determinate runs is itself evidence the finding does not
    reproduce cleanly, so the merged status is `inconclusive` rather than a
    majority vote. Discrepancy classes are the union across runs -- a
    discrepancy that shows up in even one run is real evidence, not
    something later runs can retract.
    """
    if not classifications:
        raise ValueError("aggregate_reproductions requires at least one classification")

    subjects = {c.tool_subject for c in classifications}
    if len(subjects) != 1:
        raise ValueError(f"aggregate_reproductions requires one shared tool_subject, got {subjects!r}")
    names = {c.tool_name for c in classifications}
    if len(names) != 1:
        raise ValueError(f"aggregate_reproductions requires one shared tool_name, got {names!r}")

    by_key: dict[tuple[str, str], list[PropertyClaim]] = {}
    for classification in classifications:
        for claim in classification.claims:
            by_key.setdefault((claim.property, claim.scope), []).append(claim)

    merged_claims = [_merge_claim_group(key, group) for key, group in by_key.items()]

    discrepancies = _dedupe(cls for classification in classifications for cls in classification.discrepancy_classes)
    history_digests = _dedupe_values(
        digest for classification in classifications for digest in classification.history_digests
    )

    return BehaviorClassification(
        tool_subject=subjects.pop(),
        tool_name=names.pop(),
        claims=merged_claims,
        discrepancy_classes=discrepancies,
        history_digests=history_digests,
    )


def _merge_claim_group(key: tuple[str, str], group: Sequence[PropertyClaim]) -> PropertyClaim:
    property_name, scope = key
    determinate = [claim for claim in group if claim.status in (PropertyStatus.observed_true, PropertyStatus.observed_false)]
    evidence_digests = _dedupe_values(digest for claim in group for digest in claim.evidence_digests)
    total_runs = sum(claim.runs for claim in group)

    statuses = {claim.status for claim in determinate}
    if len(statuses) == 1:
        return PropertyClaim(
            property=property_name,
            status=statuses.pop(),
            runs=total_runs,
            evidence_digests=evidence_digests,
            scope=scope,
            detail=f"reproduced across {len(determinate)}/{len(group)} runs" if determinate else None,
        )

    detail = (
        "runs disagreed on this property"
        if statuses
        else "no run produced a determinate reading"
    )
    return PropertyClaim(
        property=property_name,
        status=PropertyStatus.inconclusive,
        runs=total_runs,
        evidence_digests=evidence_digests,
        scope=scope,
        detail=detail,
    )


__all__ = [
    "BehaviorClassification",
    "DiscrepancyClass",
    "PropertyClaim",
    "PropertyStatus",
    "aggregate_reproductions",
    "classify_consistency_drift",
    "classify_discrepancies",
    "classify_history",
    "classify_idempotency",
    "classify_undeclared_side_effects",
    "compare_tool_definitions",
]
