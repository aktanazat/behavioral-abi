#!/usr/bin/env python3
"""Measure actual first-call acceptance for discovered MCP tools."""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import types

from babi.canon import Json, digest
from babi.collect import build_discover_bundle, discover, gather_runtime_digests
from babi.execute import build_client
from babi.execute.wire import dump_model
from babi.planner import synthesize_call
from babi.target import TargetSpec


async def measure_target(path: Path, timeout_seconds: float) -> dict[str, Json]:
    target = TargetSpec.model_validate_json(path.read_text())
    client_info = types.Implementation(name="babi", version="0.1.0")
    discovery = await asyncio.wait_for(discover(target, client_info=client_info), timeout=timeout_seconds)
    bundle = build_discover_bundle(discovery, runtime_digests=gather_runtime_digests(target))
    catalog = bundle["tools_list"]
    subjects = bundle["tool_subjects"]
    if not isinstance(catalog, list) or not isinstance(subjects, dict):
        raise ValueError("discovery bundle has an invalid catalog")

    results: list[dict[str, Json]] = []
    async with AsyncExitStack() as stack:
        client = build_client(target, client_info=client_info)
        await stack.enter_async_context(client)
        for definition in catalog:
            if not isinstance(definition, dict) or not isinstance(definition.get("name"), str):
                continue
            name = definition["name"]
            arguments = synthesize_call(definition, resource_prefix="babi-valid-", seed="valid-call-v0")
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
                    "tool_subject": subjects.get(name),
                    "arguments_digest": digest(arguments),
                    "accepted": accepted,
                    "error": error,
                }
            )

    accepted_count = sum(result["accepted"] is True for result in results)
    return {
        "target": target.name,
        "server_subject": bundle["server_subject"],
        "tools_attempted": len(results),
        "tools_accepted": accepted_count,
        "tools_invalid": len(results) - accepted_count,
        "results": results,
    }


async def run(paths: list[Path], timeout_seconds: float, output: Path) -> int:
    target_results: list[dict[str, Json]] = []
    for path in paths:
        try:
            target_results.append(await measure_target(path, timeout_seconds))
        except Exception as exception:
            target_results.append(
                {
                    "target": path.stem,
                    "server_subject": None,
                    "tools_attempted": 0,
                    "tools_accepted": 0,
                    "tools_invalid": 0,
                    "target_error": f"{type(exception).__name__}: {exception}",
                    "results": [],
                }
            )

    attempted = sum(int(result["tools_attempted"]) for result in target_results)
    accepted = sum(int(result["tools_accepted"]) for result in target_results)
    report: dict[str, Json] = {
        "kind": "valid-call-measurement/v0",
        "targets_attempted": len(paths),
        "targets_discovered": sum(result.get("server_subject") is not None for result in target_results),
        "tools_attempted": attempted,
        "tools_accepted": accepted,
        "tools_invalid": attempted - accepted,
        "valid_first_call_rate": None if attempted == 0 else f"{accepted / attempted:.6f}",
        "targets": target_results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"targets={len(paths)} discovered={report['targets_discovered']}")
    print(f"tools={attempted} accepted={accepted} invalid={attempted - accepted}")
    print(f"valid_first_call_rate={report['valid_first_call_rate']}")
    print(f"output={output}")
    return 0 if attempted and accepted / attempted >= 0.70 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("targets", nargs="+", type=Path)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--output", type=Path, default=Path("runs/valid-call-rate.json"))
    args = parser.parse_args()
    return asyncio.run(run(args.targets, args.timeout, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
