# Report template

This file documents the exact section order `babi.report.render_markdown`
produces from a `babi.report.Report`. It is not parsed by code; it exists so
a human (or the Day 14 external reproducer) can check a generated report
against the frozen shape without reading `report.py`. Regenerate reports with
the renderer, never by hand-editing this template.

See `report/empty-report.json` for the machine-readable shape with no
findings and a fully pending rubric (`babi.rubric.RubricInputs()`); it is
the baseline every real report is diffed against.

---

# {{title}}

`{{title}}` defaults to the neutral headline from `IMPLEMENTATION-PLAN.md`
Day 13 guidance:

> We executed the top 20 MCP servers. Here is what their annotations do not
> tell you.

It is only replaced with a stronger headline when at least one finding is
both (a) tagged `unsafe_false_positive` and (b) produced by empirical
execution (`stateful_black_box` or `local_instrumented` provenance) --
never by a static-source-review or schema-static-analysis finding alone.

Run `{{run_id}}`, generated at `{{generated_at}}`.

Rubric outcome: **{{rubric_outcome}}** (`continue` | `kill` | `pending`).

## Population and ranking method

The exact inclusion rule text, popularity-evidence description, population
size, and count of servers actually run. `population = null` means this
section has not been measured yet -- the renderer says so explicitly rather
than omitting the section.

## Rubric: pass conditions

One row per condition in `babi.rubric.PassConditionId`, in `RUBRIC.md`
order 1-7, each with its exact numerator/denominator and evidence digests.
A condition with no measured input renders `status = pending`, never
`satisfied`.

## Rubric: kill conditions

One row per condition in `babi.rubric.KillConditionId`, in `RUBRIC.md`
order 1-7. Any row with `status = triggered` means the overall rubric
outcome above is `kill`, regardless of how many pass conditions are
`satisfied`.

## Claimed-versus-observed matrix

One row per `(tool_subject, property)` claim: the vendor-declared
annotation value (`not_declared` if the vendor never set the hint) next to
the deterministically observed status, run count, and whether the two
disagree (`mismatch`).

## Discrepancy classes

Findings grouped by `babi.classify.DiscrepancyClass`, ordered by severity
label, never presented as a flat "annotations are lying" list:

1. **unsafe** -- `unsafe_false_positive`.
2. **reproducible undeclared effect** -- `undeclared_side_effect`.
3. **conservative defect** -- `conservative_false_negative`.
4. **representational defect** -- `deployment_variance`, `unobservable_claim`,
   `consistency_drift`, `wire_source_drift`.

## Reproducers and evidence links

One entry per finding: its provenance, the `ProbeHistory` digest(s) it was
derived from, its minimal-reproducer digest (if any), and the union of its
claims' evidence digests. A finding with no reproducer digest says `none`
rather than omitting the field.

## False-positive and inconclusive counts

Separately reports the empirical unsafe-false-positive count (the only one
that can justify a stronger headline) from the non-empirical count (never
folded together), plus inconclusive-claim and not-observable-claim counts
and the empirical/non-empirical finding split.

## Method limits

The fixed disclosure list from `babi.report.DEFAULT_METHOD_LIMITS`, or a
caller-supplied superset -- never fewer entries than the default.

## Glama comparison

One row per `babi.report.GlamaComparisonRow`. A row with no
`evidence_digest` is desk-research from `cited_source`, not something this
run verified, and the renderer marks it "unverified this run -- hypothesis"
rather than asserting it as fact.
