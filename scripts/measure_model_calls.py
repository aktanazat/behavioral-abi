#!/usr/bin/env python3
"""Measure iteration-2 actual call acceptance using the constrained model pass."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import types

from babi.canon import Json, digest
from babi.collect import build_discover_bundle, discover, gather_runtime_digests
from babi.evidence import EvidenceStore
from babi.execute import build_client
from babi.execute.wire import dump_model
from babi.plan import ProbePrimitive
from babi.planner import ModelPassSpec, ModelPlanner, analyze_catalog, infer_all_candidates
from babi.target import TargetSpec


async def measure_target(path: Path, timeout_seconds: float, evidence: EvidenceStore) -> dict[str, Json]:
    target = TargetSpec.model_validate_json(path.read_text())
    client_info = types.Implementation(name="babi", version="0.1.0")
    discovery = await asyncio.wait_for(discover(target, client_info=client_info), timeout=timeout_seconds)
    bundle = build_discover_bundle(discovery, runtime_digests=gather_runtime_digests(target))
    catalog = bundle["tools_list"]
    subjects = bundle["tool_subjects"]
    if not isinstance(catalog, list) or not isinstance(subjects, dict):
        raise ValueError("discovery bundle has an invalid catalog")

    candidates = infer_all_candidates(analyze_catalog(catalog, subjects))
    model_env = dict(
        os.environ,
        BABI_PLANNER_MODEL="gpt-5.4",
        BABI_CODEX_MODEL="gpt-5.4",
        BABI_CODEX_STRICT="0",
    )
    planner = ModelPlanner(
        ModelPassSpec(
            command=(sys.executable, "scripts/codex_planner.py"),
            model="gpt-5.4",
            temperature=0.0,
            timeout=1000,
            env=model_env,
        ),
        evidence=evidence,
    )
    model_result = planner.run(
        server_subject=str(bundle["server_subject"]),
        tool_catalog=catalog,
        deterministic_candidates=candidates,
    )
    call_steps = {
        step.tool_subject: step
        for step in model_result.plan.steps
        if step.primitive is ProbePrimitive.CALL_ONCE
    }

    results: list[dict[str, Json]] = []
    async with AsyncExitStack() as stack:
        client = build_client(target, client_info=client_info)
        await stack.enter_async_context(client)
        for definition in catalog:
            if not isinstance(definition, dict) or not isinstance(definition.get("name"), str):
                continue
            name = definition["name"]
            subject = subjects.get(name)
            step = call_steps.get(subject) if isinstance(subject, str) else None
            if step is None:
                results.append({"tool_subject": subject, "accepted": False, "error": "model plan omitted CALL_ONCE"})
                continue
            arguments = step.parameters.get("arguments", {})
            if not isinstance(arguments, dict):
                results.append({"tool_subject": subject, "accepted": False, "error": "model arguments are not an object"})
                continue
            try:
                response = await asyncio.wait_for(client.call_tool(name, arguments), timeout=timeout_seconds)
                wire = dump_model(response)
                accepted = wire.get("isError") is not True
                error = None if accepted else "MCP tool result has isError=true"
            except Exception as exception:
                accepted = False
                error = f"{type(exception).__name__}: {exception}"
            results.append(
                {
                    "tool_subject": subject,
                    "arguments_digest": digest(arguments),
                    "accepted": accepted,
                    "error": error,
                }
            )

    accepted_count = sum(result["accepted"] is True for result in results)
    return {
        "target": target.name,
        "server_subject": bundle["server_subject"],
        "model_plan_digest": model_result.envelope_digest,
        "tools_total": len(results),
        "tools_planned": len(call_steps),
        "tools_accepted": accepted_count,
        "tools_invalid": len(results) - accepted_count,
        "results": results,
    }


async def run(paths: list[Path], timeout_seconds: float, output: Path) -> int:
    evidence = EvidenceStore()
    targets: list[dict[str, Json]] = []
    for path in paths:
        try:
            result = await measure_target(path, timeout_seconds, evidence)
        except Exception as exception:
            result = {
                "target": path.stem,
                "server_subject": None,
                "model_plan_digest": None,
                "tools_total": 0,
                "tools_planned": 0,
                "tools_accepted": 0,
                "tools_invalid": 0,
                "target_error": f"{type(exception).__name__}: {exception}",
                "results": [],
            }
        targets.append(result)
        print(
            f"target={result['target']} planned={result['tools_planned']}/{result['tools_total']} "
            f"accepted={result['tools_accepted']}"
        )

    total = sum(int(target["tools_total"]) for target in targets)
    accepted = sum(int(target["tools_accepted"]) for target in targets)
    report: dict[str, Json] = {
        "kind": "valid-call-measurement/v0",
        "iteration": 2,
        "planner_model": "gpt-5.4",
        "targets_attempted": len(paths),
        "targets_discovered": sum(target.get("server_subject") is not None for target in targets),
        "tools_attempted": total,
        "tools_accepted": accepted,
        "tools_invalid": total - accepted,
        "valid_first_call_rate": None if total == 0 else f"{accepted / total:.6f}",
        "targets": targets,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"tools={total} accepted={accepted} invalid={total - accepted}")
    print(f"valid_first_call_rate={report['valid_first_call_rate']}")
    print(f"output={output}")
    return 0 if total and accepted / total >= 0.70 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("targets", nargs="+", type=Path)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--output", type=Path, default=Path("runs/model-valid-call-rate.json"))
    args = parser.parse_args()
    return asyncio.run(run(args.targets, args.timeout, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
