#!/usr/bin/env python3
"""Reproduce one planner-selected behavioral finding across clean runs."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from mcp import types

from babi.classify import aggregate_reproductions, classify_history
from babi.collect import build_discover_bundle, discover, gather_runtime_digests
from babi.evidence import EvidenceStore
from babi.execute import execute_plan_against_target
from babi.plan import DependencyEdge, ProbePlan, ProbePrimitive, ProbeStep, RationaleEntry, RationaleProvenance
from babi.planner import analyze_catalog, infer_all_candidates, synthesize_call
from babi.target import TargetSpec


def choose_edge(bundle):
    catalog = bundle["tools_list"]
    subjects = bundle["tool_subjects"]
    if not isinstance(catalog, list) or not isinstance(subjects, dict):
        raise ValueError("discovery bundle has an invalid catalog")
    definitions = {
        definition["name"]: definition
        for definition in catalog
        if isinstance(definition, dict) and isinstance(definition.get("name"), str)
    }
    entries = analyze_catalog(catalog, subjects)
    candidates = infer_all_candidates(entries)
    definitions_by_subject = {
        subjects[name]: definition for name, definition in definitions.items() if isinstance(subjects.get(name), str)
    }

    ranked = []
    for candidate in candidates.read_back:
        definition = definitions_by_subject.get(candidate.from_tool_subject)
        annotations = definition.get("annotations", {}) if isinstance(definition, dict) else {}
        denies_idempotency = isinstance(annotations, dict) and annotations.get("idempotentHint") is False
        ranked.append((denies_idempotency, candidate.score, candidate))
    if not ranked:
        raise ValueError("planner inferred no read-back candidate")
    _, _, selected = max(ranked, key=lambda item: (item[0], item[1]))
    return selected, definitions_by_subject[selected.from_tool_subject], definitions_by_subject[selected.to_tool_subject]


async def run(target_path: Path, run_count: int, output: Path) -> int:
    target = TargetSpec.model_validate_json(target_path.read_text())
    client_info = types.Implementation(name="babi", version="0.1.0")
    discovery = await discover(target, client_info=client_info)
    bundle = build_discover_bundle(discovery, runtime_digests=gather_runtime_digests(target))
    edge, source_definition, read_definition = choose_edge(bundle)
    source_name = source_definition["name"]
    read_name = read_definition["name"]
    arguments = synthesize_call(source_definition, resource_prefix="babi-repro-", seed="reproduction-v0")

    repeat_id = "repeat-selected-source"
    read_id = "read-back-selected-source"
    plan = ProbePlan(
        subject_digest=str(bundle["server_subject"]),
        cleanup_budget=0,
        steps=[
            ProbeStep(
                id=repeat_id,
                primitive=ProbePrimitive.REPEAT,
                tool_subject=edge.from_tool_subject,
                parameters={"arguments": arguments, "count": 2},
            ),
            ProbeStep(
                id=read_id,
                primitive=ProbePrimitive.READ_BACK,
                tool_subject=edge.to_tool_subject,
                depends_on=[repeat_id],
            ),
        ],
        observers=["read_back"],
        rationale_evidence=[
            RationaleEntry(
                edge=DependencyEdge(from_step=repeat_id, to_step=read_id),
                provenance=RationaleProvenance.SCHEMA_OVERLAP,
                detail="; ".join(edge.evidence),
            )
        ],
    )

    evidence = EvidenceStore()
    classifications = []
    for index in range(run_count):
        result = await execute_plan_against_target(
            plan,
            target,
            discovery_bundle=bundle,
            evidence=evidence,
            client_info=client_info,
            run_id=f"reproduction-{index + 1}",
            clean_state_id=f"clean-{index + 1}",
        )
        classifications.append(
            classify_history(
                result.history,
                edge.from_tool_subject,
                str(source_name),
                tool_definition=source_definition,
            )
        )

    aggregate = aggregate_reproductions(classifications)
    report = {
        "kind": "reproduction/v0",
        "target_subject": bundle["server_subject"],
        "source_tool_subject": edge.from_tool_subject,
        "read_back_tool_subject": edge.to_tool_subject,
        "planner_edge_score": str(edge.score),
        "runs": run_count,
        "classifications": [classification.model_dump(mode="json") for classification in classifications],
        "aggregate": aggregate.model_dump(mode="json"),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"selected={source_name} read_back={read_name} score={edge.score:.3f}")
    for claim in aggregate.claims:
        print(f"claim={claim.property}:{claim.status.value} runs={claim.runs} scope={claim.scope}")
    print("discrepancies=" + ",".join(item.value for item in aggregate.discrepancy_classes))
    print(f"output={output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("runs/reproduction.json"))
    args = parser.parse_args()
    return asyncio.run(run(args.target, args.runs, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
