"""Deterministic report renderer: population method, claimed-versus-observed
matrix, discrepancy-class separation, minimal reproducer/evidence links,
false-positive/inconclusive counts, method limits, and a Glama comparison
slot -- the Day 13 report deliverable from `IMPLEMENTATION-PLAN.md`.

Rendering is a pure function of a `ReportInput`: the same input always
produces byte-identical Markdown and canonical JSON. There is no wall-clock
read, network call, model call, or non-deterministic ordering in this
module; every timestamp is caller-supplied and every list is sorted before
rendering.

Empirical discipline: a finding's `provenance` records whether it came from
actually executing the target (`stateful_black_box`, `local_instrumented`)
or from reading source/schema without execution (`static_source_review`,
`schema_static_analysis`). Only empirical findings count toward the
headline unsafe-false-positive number and the default title computation --
`IMPLEMENTATION-PLAN.md` is explicit that "a model's unsupported judgment"
and "source-code-derived behavior claims" must never stand in for the
executed verdict. Non-empirical findings are still reported, but always
under their own label, never folded into the empirical count.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from babi import canon
from babi.classify import BehaviorClassification, DiscrepancyClass, PropertyStatus
from babi.rubric import RubricEvaluation, RubricOutcome

DEFAULT_TITLE = (
    "We executed the top 20 MCP servers. Here is what their annotations do not tell you."
)

DEFAULT_METHOD_LIMITS: tuple[str, ...] = (
    "A claim with status `not_applicable` means no observer existed for that "
    "property under the recorded preconditions. Absence of evidence is not "
    "evidence of the property's absence.",
    "Ancillary logging, analytics, or revision-history changes alone do not "
    "make an operation non-idempotent; RFC 9110 permits ancillary side "
    "effects for idempotent operations.",
    "A single planner run establishes plan stability only when compared "
    "against an independent second run; planner agreement is evidence about "
    "plan stability, not about the target's actual behavior.",
    "Static-source-review and schema-static-analysis findings are reported "
    "for completeness but are never counted as empirical; only stateful "
    "black-box or local-instrumented execution evidence is empirical.",
    "Best-effort protocol notifications (`tools/list_changed`, "
    "`subscriptions/listen`) are treated as hints, not events; consistency "
    "findings compare polled digests, not notification delivery.",
    "A property observed under one sandbox account, region, or deployment "
    "may not generalize to another; see `deployment_variance` findings.",
)


class FindingProvenance(str, Enum):
    """How a finding's evidence was produced. Only the first two members
    are execution evidence; the report must never call the other two
    empirical."""

    STATEFUL_BLACK_BOX = "stateful_black_box"
    LOCAL_INSTRUMENTED = "local_instrumented"
    STATIC_SOURCE_REVIEW = "static_source_review"
    SCHEMA_STATIC_ANALYSIS = "schema_static_analysis"

    @property
    def is_empirical(self) -> bool:
        return self in (FindingProvenance.STATEFUL_BLACK_BOX, FindingProvenance.LOCAL_INSTRUMENTED)


_DISCREPANCY_LABELS: dict[DiscrepancyClass, str] = {
    DiscrepancyClass.unsafe_false_positive: "unsafe",
    DiscrepancyClass.undeclared_side_effect: "reproducible undeclared effect",
    DiscrepancyClass.conservative_false_negative: "conservative defect",
    DiscrepancyClass.deployment_variance: "representational defect",
    DiscrepancyClass.unobservable_claim: "representational defect",
    DiscrepancyClass.consistency_drift: "representational defect",
    DiscrepancyClass.wire_source_drift: "representational defect",
}

_DISCREPANCY_PRIORITY: dict[str, int] = {
    "unsafe": 0,
    "reproducible undeclared effect": 1,
    "conservative defect": 2,
    "representational defect": 3,
}

_ANNOTATION_PROPERTIES = ("read_only", "destructive", "idempotent", "open_world")


class VendorAnnotations(BaseModel):
    """Annotations as declared by the target's `tools/list` response, keyed
    to the same `tool_subject` a `BehaviorClassification` addresses. `None`
    means the vendor did not declare that hint, not that it is false."""

    model_config = ConfigDict(extra="forbid")

    tool_subject: str
    tool_name: str
    read_only: bool | None = None
    destructive: bool | None = None
    idempotent: bool | None = None
    open_world: bool | None = None
    evidence_digest: str | None = None

    def declared(self, property_name: str) -> bool | None:
        return getattr(self, property_name, None) if property_name in _ANNOTATION_PROPERTIES else None


class FindingRecord(BaseModel):
    """One report-level finding: the executor/classifier's deterministic
    classification of a tool subject, plus the report-specific facts the
    classifier does not carry -- how the evidence was produced and what the
    vendor declared."""

    model_config = ConfigDict(extra="forbid")

    classification: BehaviorClassification
    provenance: FindingProvenance
    vendor_declared: VendorAnnotations
    reproducer_digest: str | None = None


class PopulationMethod(BaseModel):
    """Day 13 "population and ranking method" section."""

    model_config = ConfigDict(extra="forbid")

    population_size: int = Field(ge=0)
    servers_run: int = Field(ge=0)
    inclusion_rules: str
    popularity_evidence: str
    sandbox_targets: list[str] = Field(default_factory=list)
    evidence_digest: str | None = None


class GlamaComparisonRow(BaseModel):
    """One row of the Glama comparison slot. `IMPLEMENTATION-PLAN.md`: "This
    difference must be demonstrated, not asserted." A row with no
    `evidence_digest` is a desk-research hypothesis from `cited_source`, not
    a claim this run verified -- the renderer must label it as such."""

    model_config = ConfigDict(extra="forbid")

    dimension: str
    glama_covers: bool | None = None
    babi_covers: bool | None = None
    detail: str
    cited_source: str | None = None
    evidence_digest: str | None = None


DEFAULT_GLAMA_COMPARISON: tuple[GlamaComparisonRow, ...] = (
    GlamaComparisonRow(
        dimension="syscall/network observation in an isolated runtime",
        glama_covers=True,
        babi_covers=None,
        detail="Glama executes open-source MCP servers in Firecracker and observes syscalls and network activity.",
        cited_source="https://glama.ai/mcp/methodology",
    ),
    GlamaComparisonRow(
        dimension="schema drift rescans on commit",
        glama_covers=True,
        babi_covers=None,
        detail="Glama rescans on commits and records schema drift.",
        cited_source="https://glama.ai/mcp/methodology",
    ),
    GlamaComparisonRow(
        dimension="signed semantic contract for idempotency",
        glama_covers=None,
        babi_covers=None,
        detail="Not found published on the methodology page as of the plan's research date; unverified this run.",
        cited_source="https://glama.ai/mcp/methodology",
    ),
    GlamaComparisonRow(
        dimension="signed semantic contract for reversibility",
        glama_covers=None,
        babi_covers=None,
        detail="Not found published on the methodology page as of the plan's research date; unverified this run.",
        cited_source="https://glama.ai/mcp/methodology",
    ),
    GlamaComparisonRow(
        dimension="signed semantic contract for hidden preconditions",
        glama_covers=None,
        babi_covers=None,
        detail="Not found published on the methodology page as of the plan's research date; unverified this run.",
        cited_source="https://glama.ai/mcp/methodology",
    ),
    GlamaComparisonRow(
        dimension="signed semantic contract for consistency timing",
        glama_covers=None,
        babi_covers=None,
        detail="Not found published on the methodology page as of the plan's research date; unverified this run.",
        cited_source="https://glama.ai/mcp/methodology",
    ),
)


class ReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    generated_at: str
    rubric: RubricEvaluation
    population: PopulationMethod | None = None
    findings: list[FindingRecord] = Field(default_factory=list)
    glama_comparison: list[GlamaComparisonRow] = Field(
        default_factory=lambda: list(DEFAULT_GLAMA_COMPARISON)
    )
    method_limits: list[str] = Field(default_factory=lambda: list(DEFAULT_METHOD_LIMITS))
    title_override: str | None = None


class AnnotationObservedRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_subject: str
    tool_name: str
    property: str
    vendor_declared: bool | None
    observed_status: PropertyStatus
    runs: int
    mismatch: bool
    evidence_digests: list[str]


class DiscrepancyGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discrepancy_class: DiscrepancyClass
    label: str
    tool_subjects: list[str]


class ReproducerLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_subject: str
    tool_name: str
    provenance: FindingProvenance
    history_digests: list[str]
    reproducer_digest: str | None
    evidence_digests: list[str]


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    run_id: str
    generated_at: str
    rubric_outcome: RubricOutcome
    rubric: RubricEvaluation
    population: PopulationMethod | None
    annotation_matrix: list[AnnotationObservedRow]
    discrepancy_groups: list[DiscrepancyGroup]
    reproducers: list[ReproducerLink]
    empirical_unsafe_false_positive_count: int
    non_empirical_unsafe_false_positive_count: int
    inconclusive_claim_count: int
    not_observable_claim_count: int
    empirical_finding_count: int
    non_empirical_finding_count: int
    method_limits: list[str]
    glama_comparison: list[GlamaComparisonRow]


def _mismatch(vendor_declared: bool | None, observed_status: PropertyStatus) -> bool:
    if vendor_declared is None:
        return False
    if vendor_declared and observed_status is PropertyStatus.observed_false:
        return True
    if not vendor_declared and observed_status is PropertyStatus.observed_true:
        return True
    return False


def _build_annotation_matrix(findings: list[FindingRecord]) -> list[AnnotationObservedRow]:
    rows: list[AnnotationObservedRow] = []
    for finding in findings:
        classification = finding.classification
        for claim in sorted(classification.claims, key=lambda c: c.property):
            declared = finding.vendor_declared.declared(claim.property)
            rows.append(
                AnnotationObservedRow(
                    tool_subject=classification.tool_subject,
                    tool_name=classification.tool_name,
                    property=claim.property,
                    vendor_declared=declared,
                    observed_status=claim.status,
                    runs=claim.runs,
                    mismatch=_mismatch(declared, claim.status),
                    evidence_digests=list(claim.evidence_digests),
                )
            )
    return sorted(rows, key=lambda r: (r.tool_subject, r.property))


def _build_discrepancy_groups(findings: list[FindingRecord]) -> list[DiscrepancyGroup]:
    by_class: dict[DiscrepancyClass, set[str]] = {}
    for finding in findings:
        for discrepancy_class in finding.classification.discrepancy_classes:
            by_class.setdefault(discrepancy_class, set()).add(finding.classification.tool_subject)

    groups = [
        DiscrepancyGroup(
            discrepancy_class=discrepancy_class,
            label=_DISCREPANCY_LABELS[discrepancy_class],
            tool_subjects=sorted(subjects),
        )
        for discrepancy_class, subjects in by_class.items()
    ]
    return sorted(
        groups,
        key=lambda g: (_DISCREPANCY_PRIORITY[g.label], g.discrepancy_class.value),
    )


def _build_reproducers(findings: list[FindingRecord]) -> list[ReproducerLink]:
    links = [
        ReproducerLink(
            tool_subject=finding.classification.tool_subject,
            tool_name=finding.classification.tool_name,
            provenance=finding.provenance,
            history_digests=list(finding.classification.history_digests),
            reproducer_digest=finding.reproducer_digest,
            evidence_digests=sorted(
                {digest for claim in finding.classification.claims for digest in claim.evidence_digests}
            ),
        )
        for finding in findings
    ]
    return sorted(links, key=lambda link: link.tool_subject)


def _compute_title(findings: list[FindingRecord], title_override: str | None) -> str:
    if title_override is not None:
        return title_override
    empirical_unsafe_subjects = {
        finding.classification.tool_subject
        for finding in findings
        if finding.provenance.is_empirical
        and DiscrepancyClass.unsafe_false_positive in finding.classification.discrepancy_classes
    }
    if empirical_unsafe_subjects:
        count = len(empirical_unsafe_subjects)
        plural = "server" if count == 1 else "servers"
        return (
            f"Executed evidence surfaced unsafe false-positive tool annotations on "
            f"{count} MCP {plural}."
        )
    return DEFAULT_TITLE


def build_report(report_input: ReportInput) -> Report:
    """Pure, deterministic transform from measured facts to a rendered
    `Report`. Calling this twice with equal input produces equal output."""
    findings = report_input.findings

    empirical_unsafe_subjects = {
        finding.classification.tool_subject
        for finding in findings
        if finding.provenance.is_empirical
        and DiscrepancyClass.unsafe_false_positive in finding.classification.discrepancy_classes
    }
    non_empirical_unsafe_subjects = {
        finding.classification.tool_subject
        for finding in findings
        if not finding.provenance.is_empirical
        and DiscrepancyClass.unsafe_false_positive in finding.classification.discrepancy_classes
    }

    inconclusive_claim_count = sum(
        1
        for finding in findings
        for claim in finding.classification.claims
        if claim.status is PropertyStatus.inconclusive
    )
    not_observable_claim_count = sum(
        1
        for finding in findings
        for claim in finding.classification.claims
        if claim.status is PropertyStatus.not_applicable
    )
    empirical_finding_count = sum(1 for finding in findings if finding.provenance.is_empirical)
    non_empirical_finding_count = len(findings) - empirical_finding_count

    return Report(
        title=_compute_title(findings, report_input.title_override),
        run_id=report_input.run_id,
        generated_at=report_input.generated_at,
        rubric_outcome=report_input.rubric.outcome,
        rubric=report_input.rubric,
        population=report_input.population,
        annotation_matrix=_build_annotation_matrix(findings),
        discrepancy_groups=_build_discrepancy_groups(findings),
        reproducers=_build_reproducers(findings),
        empirical_unsafe_false_positive_count=len(empirical_unsafe_subjects),
        non_empirical_unsafe_false_positive_count=len(non_empirical_unsafe_subjects),
        inconclusive_claim_count=inconclusive_claim_count,
        not_observable_claim_count=not_observable_claim_count,
        empirical_finding_count=empirical_finding_count,
        non_empirical_finding_count=non_empirical_finding_count,
        method_limits=list(report_input.method_limits),
        glama_comparison=list(report_input.glama_comparison),
    )


def render_json(report: Report) -> dict[str, canon.Json]:
    """Machine-readable report data, as a plain JSON-compatible dict."""
    return report.model_dump(mode="json")


def render_json_bytes(report: Report) -> bytes:
    """Canonical (RFC 8785) JSON bytes -- deterministic across processes and
    suitable for content-addressed storage via `babi.evidence.EvidenceStore`."""
    return canon.canonical_json(render_json(report))


def _render_glama_row(row: GlamaComparisonRow) -> str:
    def _cell(value: bool | None) -> str:
        if value is None:
            return "unverified"
        return "yes" if value else "no"

    verified = "verified this run" if row.evidence_digest else "unverified this run -- hypothesis"
    source = f" (source: {row.cited_source})" if row.cited_source else ""
    return f"| {row.dimension} | {_cell(row.glama_covers)} | {_cell(row.babi_covers)} | {row.detail} {verified}{source} |"


def render_markdown(report: Report) -> str:
    """Deterministic Markdown rendering of `report`."""
    lines: list[str] = []
    lines.append(f"# {report.title}")
    lines.append("")
    lines.append(f"Run `{report.run_id}`, generated at `{report.generated_at}`.")
    lines.append("")
    lines.append(f"Rubric outcome: **{report.rubric_outcome.value}**.")
    lines.append("")

    lines.append("## Population and ranking method")
    lines.append("")
    if report.population is None:
        lines.append("Population method has not been measured for this run.")
    else:
        pop = report.population
        lines.append(f"- Population size: {pop.population_size}")
        lines.append(f"- Servers run: {pop.servers_run}")
        lines.append(f"- Inclusion rules: {pop.inclusion_rules}")
        lines.append(f"- Popularity evidence: {pop.popularity_evidence}")
        if pop.sandbox_targets:
            lines.append(f"- Sandbox targets: {', '.join(pop.sandbox_targets)}")
    lines.append("")

    lines.append("## Rubric: pass conditions")
    lines.append("")
    lines.append("| condition | status | numerator | denominator | detail |")
    lines.append("| --- | --- | --- | --- | --- |")
    for result in report.rubric.pass_conditions:
        lines.append(
            f"| {result.condition_id.value} | {result.status.value} | "
            f"{result.numerator if result.numerator is not None else '-'} | "
            f"{result.denominator if result.denominator is not None else '-'} | {result.detail} |"
        )
    lines.append("")

    lines.append("## Rubric: kill conditions")
    lines.append("")
    lines.append("| condition | status | numerator | denominator | detail |")
    lines.append("| --- | --- | --- | --- | --- |")
    for kill_result in report.rubric.kill_conditions:
        lines.append(
            f"| {kill_result.condition_id.value} | {kill_result.status.value} | "
            f"{kill_result.numerator if kill_result.numerator is not None else '-'} | "
            f"{kill_result.denominator if kill_result.denominator is not None else '-'} | {kill_result.detail} |"
        )
    lines.append("")

    lines.append("## Claimed-versus-observed matrix")
    lines.append("")
    if not report.annotation_matrix:
        lines.append("No annotation claims recorded for this run.")
    else:
        lines.append("| tool_subject | tool_name | property | vendor_declared | observed | runs | mismatch |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for row in report.annotation_matrix:
            declared = "not_declared" if row.vendor_declared is None else str(row.vendor_declared)
            lines.append(
                f"| {row.tool_subject} | {row.tool_name} | {row.property} | {declared} | "
                f"{row.observed_status.value} | {row.runs} | {row.mismatch} |"
            )
    lines.append("")

    lines.append("## Discrepancy classes")
    lines.append("")
    if not report.discrepancy_groups:
        lines.append("No discrepancies recorded for this run.")
    else:
        for group in report.discrepancy_groups:
            lines.append(f"- **{group.label}** ({group.discrepancy_class.value}): {', '.join(group.tool_subjects)}")
    lines.append("")

    lines.append("## Reproducers and evidence links")
    lines.append("")
    if not report.reproducers:
        lines.append("No reproducers recorded for this run.")
    else:
        for link in report.reproducers:
            digests = ", ".join(link.evidence_digests) if link.evidence_digests else "none"
            history = ", ".join(link.history_digests) if link.history_digests else "none"
            reproducer = link.reproducer_digest or "none"
            lines.append(
                f"- `{link.tool_subject}` ({link.tool_name}), provenance={link.provenance.value}: "
                f"history={history}; reproducer={reproducer}; evidence={digests}"
            )
    lines.append("")

    lines.append("## False-positive and inconclusive counts")
    lines.append("")
    lines.append(
        f"- Empirical unsafe false positives: {report.empirical_unsafe_false_positive_count}"
    )
    lines.append(
        f"- Non-empirical (static/source-derived) unsafe false-positive candidates: "
        f"{report.non_empirical_unsafe_false_positive_count} (not counted as empirical)"
    )
    lines.append(f"- Inconclusive claims: {report.inconclusive_claim_count}")
    lines.append(f"- Not-observable claims: {report.not_observable_claim_count}")
    lines.append(f"- Empirical findings: {report.empirical_finding_count}")
    lines.append(f"- Non-empirical findings: {report.non_empirical_finding_count}")
    lines.append("")

    lines.append("## Method limits")
    lines.append("")
    for limit in report.method_limits:
        lines.append(f"- {limit}")
    lines.append("")

    lines.append("## Glama comparison")
    lines.append("")
    lines.append("| dimension | glama | babi | detail |")
    lines.append("| --- | --- | --- | --- |")
    for row in report.glama_comparison:
        lines.append(_render_glama_row(row))
    lines.append("")

    return "\n".join(lines)
