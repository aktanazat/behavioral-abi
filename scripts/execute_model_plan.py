#!/usr/bin/env python3
"""Execute model-planned CALL_ONCE steps admitted by generic risk policy."""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import types

from babi.collect import build_discover_bundle, discover, gather_runtime_digests
from babi.execute import build_client
from babi.execute.wire import dump_model
from babi.plan import ProbePlan, ProbePrimitive
from babi.target import TargetSpec


async def run(target_path: Path, plan_path: Path, timeout: float, output: Path) -> int:
    target = TargetSpec.model_validate_json(target_path.read_text())
    envelope = json.loads(plan_path.read_text())
    plan = ProbePlan.model_validate(envelope["plan"])
    client_info = types.Implementation(name="babi", version="0.1.0")
    discovery = await discover(target, client_info=client_info)
    bundle = build_discover_bundle(discovery, runtime_digests=gather_runtime_digests(target))
    definitions = {
        bundle["tool_subjects"][definition["name"]]: definition
        for definition in bundle["tools_list"]
    }

    results = []
    async with AsyncExitStack() as stack:
        client = build_client(target, client_info=client_info)
        await stack.enter_async_context(client)
        for step in plan.steps:
            if step.primitive is not ProbePrimitive.CALL_ONCE:
                continue
            definition = definitions.get(step.tool_subject)
            annotations = definition.get("annotations", {}) if isinstance(definition, dict) else {}
            admitted = isinstance(annotations, dict) and annotations.get("readOnlyHint") is True
            if not admitted:
                results.append({"tool_subject": step.tool_subject, "status": "policy_blocked"})
                continue
            arguments = step.parameters.get("arguments", step.parameters)
            if not isinstance(arguments, dict):
                results.append({"tool_subject": step.tool_subject, "status": "invalid_arguments"})
                continue
            try:
                response = await asyncio.wait_for(client.call_tool(definition["name"], arguments), timeout=timeout)
                wire = dump_model(response)
                status = "accepted" if wire.get("isError") is not True else "mcp_error"
            except Exception as exception:
                status = f"exception:{type(exception).__name__}"
            results.append({"tool_subject": step.tool_subject, "status": status})

    accepted = sum(result["status"] == "accepted" for result in results)
    attempted = sum(result["status"] != "policy_blocked" for result in results)
    report = {
        "kind": "safe-model-plan-execution/v0",
        "target_subject": bundle["server_subject"],
        "plan_envelope_digest": envelope.get("response_digest"),
        "steps_total": len(results),
        "steps_admitted": attempted,
        "steps_accepted": accepted,
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"steps={len(results)} admitted={attempted} accepted={accepted}")
    print(f"output={output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return asyncio.run(run(args.target, args.plan, args.timeout, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
