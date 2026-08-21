from pathlib import Path

import pytest
from pydantic import ValidationError

from babi.classify import BehaviorClassification, DiscrepancyClass, PropertyClaim, PropertyStatus
from babi.report import (
    DEFAULT_TITLE,
    FindingProvenance,
    FindingRecord,
    GlamaComparisonRow,
    PopulationMethod,
    ReportInput,
    VendorAnnotations,
    build_report,
    render_json,
    render_json_bytes,
    render_markdown,
)
from babi.rubric import RubricInputs, evaluate_rubric


def _empty_rubric_evaluation():
    return evaluate_rubric(RubricInputs())


def _finding(
    *,
    tool_subject: str = "sha256:" + "a" * 64,
    tool_name: str = "example_tool",
    status: PropertyStatus = PropertyStatus.observed_true,
    discrepancy_classes: list[DiscrepancyClass] | None = None,
    provenance: FindingProvenance = FindingProvenance.STATEFUL_BLACK_BOX,
    read_only_declared: bool | None = True,
    runs: int = 3,
) -> FindingRecord:
    classification = BehaviorClassification(
        tool_subject=tool_subject,
        tool_name=tool_name,
        claims=[
            PropertyClaim(
                property="read_only",
                status=status,
                runs=runs,
                evidence_digests=["sha256:" + "b" * 64],
                scope="default sandbox account",
            )
        ],
        discrepancy_classes=discrepancy_classes or [],
        history_digests=["sha256:" + "c" * 64],
    )
    return FindingRecord(
        classification=classification,
        provenance=provenance,
        vendor_declared=VendorAnnotations(
            tool_subject=tool_subject, tool_name=tool_name, read_only=read_only_declared
        ),
        reproducer_digest="sha256:" + "d" * 64,
    )


def test_default_title_with_no_findings() -> None:
    report_input = ReportInput(
        run_id="run-1", generated_at="2026-08-20T00:00:00Z", rubric=_empty_rubric_evaluation()
    )
    report = build_report(report_input)
    assert report.title == DEFAULT_TITLE


def test_default_title_survives_non_empirical_unsafe_finding() -> None:
    finding = _finding(
        status=PropertyStatus.observed_false,
        discrepancy_classes=[DiscrepancyClass.unsafe_false_positive],
        provenance=FindingProvenance.STATIC_SOURCE_REVIEW,
    )
    report_input = ReportInput(
        run_id="run-2",
        generated_at="2026-08-20T00:00:00Z",
        rubric=_empty_rubric_evaluation(),
        findings=[finding],
    )
    report = build_report(report_input)
    assert report.title == DEFAULT_TITLE
    assert report.empirical_unsafe_false_positive_count == 0
    assert report.non_empirical_unsafe_false_positive_count == 1


def test_title_changes_only_for_empirical_unsafe_finding() -> None:
    finding = _finding(
        status=PropertyStatus.observed_false,
        discrepancy_classes=[DiscrepancyClass.unsafe_false_positive],
        provenance=FindingProvenance.STATEFUL_BLACK_BOX,
    )
    report_input = ReportInput(
        run_id="run-3",
        generated_at="2026-08-20T00:00:00Z",
        rubric=_empty_rubric_evaluation(),
        findings=[finding],
    )
    report = build_report(report_input)
    assert report.title != DEFAULT_TITLE
    assert "unsafe false-positive" in report.title
    assert report.empirical_unsafe_false_positive_count == 1
    assert report.non_empirical_unsafe_false_positive_count == 0


def test_title_override_takes_precedence() -> None:
    report_input = ReportInput(
        run_id="run-4",
        generated_at="2026-08-20T00:00:00Z",
        rubric=_empty_rubric_evaluation(),
        title_override="Custom operator-supplied title",
    )
    report = build_report(report_input)
    assert report.title == "Custom operator-supplied title"


def test_annotation_matrix_flags_mismatch() -> None:
    finding = _finding(status=PropertyStatus.observed_false, read_only_declared=True)
    report_input = ReportInput(
        run_id="run-5",
        generated_at="2026-08-20T00:00:00Z",
        rubric=_empty_rubric_evaluation(),
        findings=[finding],
    )
    report = build_report(report_input)
    assert len(report.annotation_matrix) == 1
    row = report.annotation_matrix[0]
    assert row.vendor_declared is True
    assert row.observed_status is PropertyStatus.observed_false
    assert row.mismatch is True


def test_annotation_matrix_no_mismatch_when_not_declared() -> None:
    finding = _finding(status=PropertyStatus.observed_false, read_only_declared=None)
    report_input = ReportInput(
        run_id="run-6",
        generated_at="2026-08-20T00:00:00Z",
        rubric=_empty_rubric_evaluation(),
        findings=[finding],
    )
    report = build_report(report_input)
    row = report.annotation_matrix[0]
    assert row.vendor_declared is None
    assert row.mismatch is False


def test_discrepancy_groups_ordered_unsafe_first() -> None:
    unsafe = _finding(
        tool_subject="sha256:" + "1" * 64,
        discrepancy_classes=[DiscrepancyClass.conservative_false_negative],
    )
    also_unsafe = _finding(
        tool_subject="sha256:" + "2" * 64,
        discrepancy_classes=[DiscrepancyClass.unsafe_false_positive],
    )
    report_input = ReportInput(
        run_id="run-7",
        generated_at="2026-08-20T00:00:00Z",
        rubric=_empty_rubric_evaluation(),
        findings=[unsafe, also_unsafe],
    )
    report = build_report(report_input)
    assert report.discrepancy_groups[0].discrepancy_class is DiscrepancyClass.unsafe_false_positive
    assert report.discrepancy_groups[0].label == "unsafe"
    assert report.discrepancy_groups[1].discrepancy_class is DiscrepancyClass.conservative_false_negative
    assert report.discrepancy_groups[1].label == "conservative defect"


def test_inconclusive_and_not_observable_counts() -> None:
    inconclusive = _finding(tool_subject="sha256:" + "3" * 64, status=PropertyStatus.inconclusive)
    not_observable = _finding(tool_subject="sha256:" + "4" * 64, status=PropertyStatus.not_applicable)
    report_input = ReportInput(
        run_id="run-8",
        generated_at="2026-08-20T00:00:00Z",
        rubric=_empty_rubric_evaluation(),
        findings=[inconclusive, not_observable],
    )
    report = build_report(report_input)
    assert report.inconclusive_claim_count == 1
    assert report.not_observable_claim_count == 1


def test_empirical_vs_non_empirical_finding_split() -> None:
    empirical = _finding(tool_subject="sha256:" + "5" * 64, provenance=FindingProvenance.LOCAL_INSTRUMENTED)
    static = _finding(tool_subject="sha256:" + "6" * 64, provenance=FindingProvenance.SCHEMA_STATIC_ANALYSIS)
    report_input = ReportInput(
        run_id="run-9",
        generated_at="2026-08-20T00:00:00Z",
        rubric=_empty_rubric_evaluation(),
        findings=[empirical, static],
    )
    report = build_report(report_input)
    assert report.empirical_finding_count == 1
    assert report.non_empirical_finding_count == 1


def test_reproducers_include_history_and_evidence_digests() -> None:
    finding = _finding()
    report_input = ReportInput(
        run_id="run-10",
        generated_at="2026-08-20T00:00:00Z",
        rubric=_empty_rubric_evaluation(),
        findings=[finding],
    )
    report = build_report(report_input)
    link = report.reproducers[0]
    assert link.history_digests == ["sha256:" + "c" * 64]
    assert link.reproducer_digest == "sha256:" + "d" * 64
    assert "sha256:" + "b" * 64 in link.evidence_digests


def test_glama_comparison_defaults_present_and_unverified() -> None:
    report_input = ReportInput(
        run_id="run-11", generated_at="2026-08-20T00:00:00Z", rubric=_empty_rubric_evaluation()
    )
    report = build_report(report_input)
    assert len(report.glama_comparison) > 0
    assert all(row.evidence_digest is None for row in report.glama_comparison)


def test_default_method_limits_always_present() -> None:
    report_input = ReportInput(
        run_id="run-12", generated_at="2026-08-20T00:00:00Z", rubric=_empty_rubric_evaluation()
    )
    report = build_report(report_input)
    assert len(report.method_limits) >= 6
    assert any("not_applicable" in limit for limit in report.method_limits)
    assert any("empirical" in limit for limit in report.method_limits)


def test_rendering_is_deterministic() -> None:
    finding = _finding()
    report_input = ReportInput(
        run_id="run-13",
        generated_at="2026-08-20T00:00:00Z",
        rubric=_empty_rubric_evaluation(),
        findings=[finding],
    )
    report_a = build_report(report_input)
    report_b = build_report(report_input)
    assert render_markdown(report_a) == render_markdown(report_b)
    assert render_json_bytes(report_a) == render_json_bytes(report_b)
    assert render_json(report_a) == render_json(report_b)


def test_rendering_is_stable_across_finding_order() -> None:
    first = _finding(tool_subject="sha256:" + "7" * 64, tool_name="aaa")
    second = _finding(tool_subject="sha256:" + "8" * 64, tool_name="bbb")
    forward = ReportInput(
        run_id="run-14",
        generated_at="2026-08-20T00:00:00Z",
        rubric=_empty_rubric_evaluation(),
        findings=[first, second],
    )
    backward = ReportInput(
        run_id="run-14",
        generated_at="2026-08-20T00:00:00Z",
        rubric=_empty_rubric_evaluation(),
        findings=[second, first],
    )
    assert render_json_bytes(build_report(forward)) == render_json_bytes(build_report(backward))


def test_markdown_includes_no_unsupported_headline_by_default() -> None:
    report_input = ReportInput(
        run_id="run-15", generated_at="2026-08-20T00:00:00Z", rubric=_empty_rubric_evaluation()
    )
    markdown = render_markdown(build_report(report_input))
    assert "lying" not in markdown.lower()
    assert DEFAULT_TITLE in markdown


def test_markdown_labels_unverified_glama_rows() -> None:
    report_input = ReportInput(
        run_id="run-16",
        generated_at="2026-08-20T00:00:00Z",
        rubric=_empty_rubric_evaluation(),
        glama_comparison=[
            GlamaComparisonRow(
                dimension="test dimension",
                glama_covers=True,
                babi_covers=False,
                detail="desk research only",
                cited_source="https://example.test/methodology",
            )
        ],
    )
    markdown = render_markdown(build_report(report_input))
    assert "unverified this run" in markdown


def test_population_missing_renders_explicit_note() -> None:
    report_input = ReportInput(
        run_id="run-17", generated_at="2026-08-20T00:00:00Z", rubric=_empty_rubric_evaluation()
    )
    report = build_report(report_input)
    assert report.population is None
    markdown = render_markdown(report)
    assert "has not been measured" in markdown


def test_population_present_renders_fields() -> None:
    report_input = ReportInput(
        run_id="run-18",
        generated_at="2026-08-20T00:00:00Z",
        rubric=_empty_rubric_evaluation(),
        population=PopulationMethod(
            population_size=20,
            servers_run=20,
            inclusion_rules="publicly installable, maintained, popularity-evidenced",
            popularity_evidence="npm/PyPI downloads and GitHub stars",
            sandbox_targets=["stripe", "github", "cloudflare"],
        ),
    )
    markdown = render_markdown(build_report(report_input))
    assert "npm/PyPI downloads and GitHub stars" in markdown


def test_extra_field_forbidden_on_report_input() -> None:
    with pytest.raises(ValidationError):
        ReportInput.model_validate(
            {
                "run_id": "run-19",
                "generated_at": "2026-08-20T00:00:00Z",
                "rubric": _empty_rubric_evaluation().model_dump(mode="json"),
                "unexpected_field": "nope",
            }
        )


def test_rubric_outcome_matches_rubric_evaluation() -> None:
    evaluation = _empty_rubric_evaluation()
    report_input = ReportInput(run_id="run-20", generated_at="2026-08-20T00:00:00Z", rubric=evaluation)
    report = build_report(report_input)
    assert report.rubric_outcome == evaluation.outcome


def test_empty_report_baseline_matches_committed_file() -> None:
    report_input = ReportInput(
        run_id="00000000-0000-0000-0000-000000000000",
        generated_at="1970-01-01T00:00:00Z",
        rubric=_empty_rubric_evaluation(),
    )
    expected = render_json_bytes(build_report(report_input)) + b"\n"
    baseline_path = Path(__file__).resolve().parent.parent / "report" / "empty-report.json"
    assert baseline_path.read_bytes() == expected
