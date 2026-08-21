#!/usr/bin/env python3
"""Build a reproduced non-idempotent contract for the runtime-gate demo."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp.client import Client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.nonidempotent_server import CLIENT_INFO, build_server, discovery_bundle
from babi.attest import DSSE_PAYLOAD_TYPE_IN_TOTO, pae
from babi.canon import canonical_json
from babi.classify import aggregate_reproductions, classify_history
from babi.contract import ContractEmitter
from babi.evidence import EvidenceStore
from babi.execute import execute_plan
from babi.plan import DependencyEdge, ProbePlan, ProbePrimitive, ProbeStep, RationaleEntry, RationaleProvenance
from babi.planner import analyze_catalog, infer_all_candidates, synthesize_call


async def run(output_dir: Path) -> int:
    evidence = EvidenceStore()
    classifications = []
    stable_bundle = None

    for index in range(3):
        server = build_server()
        async with Client(server=server, mode="legacy", client_info=CLIENT_INFO) as client:
            bundle = await discovery_bundle(client)
            stable_bundle = stable_bundle or bundle
            entries = analyze_catalog(bundle["tools_list"], bundle["tool_subjects"])
            candidates = infer_all_candidates(entries)
            edge = max(candidates.read_back, key=lambda candidate: candidate.score)
            definitions = {
                bundle["tool_subjects"][tool["name"]]: tool
                for tool in bundle["tools_list"]
            }
            source_definition = definitions[edge.from_tool_subject]
            arguments = synthesize_call(source_definition, resource_prefix="babi-deny-", seed="deny-demo-v0")
            repeat_id = "repeat-selected-source"
            read_id = "read-back-selected-source"
            plan = ProbePlan(
                subject_digest=bundle["server_subject"],
                cleanup_budget=0,
                observers=["read_back"],
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
                rationale_evidence=[
                    RationaleEntry(
                        edge=DependencyEdge(from_step=repeat_id, to_step=read_id),
                        provenance=RationaleProvenance.SCHEMA_OVERLAP,
                        detail="; ".join(edge.evidence),
                    )
                ],
            )
            execution = await execute_plan(
                plan,
                client=client,
                discovery_bundle=bundle,
                evidence=evidence,
                run_id=f"deny-demo-{index + 1}",
                clean_state_id=f"deny-clean-{index + 1}",
            )
            classifications.append(
                classify_history(
                    execution.history,
                    edge.from_tool_subject,
                    source_definition["name"],
                    tool_definition=source_definition,
                )
            )

    assert stable_bundle is not None
    aggregate = aggregate_reproductions(classifications)
    reproduction = {
        "kind": "reproduction/v0",
        "runs": 3,
        "aggregate": aggregate.model_dump(mode="json"),
    }
    reproduction_digest = evidence.put_json(reproduction)
    statement = ContractEmitter(evidence).emit(
        aggregate,
        stable_bundle,
        list(aggregate.history_digests),
        reproduction_digest,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "reproduction.json").write_bytes(canonical_json(reproduction) + b"\n")
    (output_dir / "contract.json").write_bytes(canonical_json(statement) + b"\n")
    (output_dir / "contract.dsse-pae").write_bytes(
        pae(DSSE_PAYLOAD_TYPE_IN_TOTO, canonical_json(statement))
    )
    print("claim=" + ",".join(f"{claim.property}:{claim.status.value}" for claim in aggregate.claims))
    print("discrepancies=" + ",".join(value.value for value in aggregate.discrepancy_classes))
    print(f"history_count={len(aggregate.history_digests)}")
    print(f"output={output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(Path("runs/deny-demo"))))
