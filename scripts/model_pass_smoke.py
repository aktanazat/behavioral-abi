#!/usr/bin/env python3
"""Run two constrained planner passes against one discovered target."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from mcp import types

from babi.canon import canonical_json
from babi.collect import build_discover_bundle, discover, gather_runtime_digests
from babi.evidence import EvidenceStore
from babi.manifest import SandboxManifest
from babi.planner import ModelPassSpec, ModelPlanner, analyze_catalog, compare_plans, infer_all_candidates
from babi.target import TargetSpec


async def run(target_path: Path, sandbox_path: Path | None, output_dir: Path) -> int:
    target = TargetSpec.model_validate_json(target_path.read_text())
    sandbox = SandboxManifest.model_validate_json(sandbox_path.read_text()) if sandbox_path else None
    discovery = await discover(target, client_info=types.Implementation(name="babi", version="0.1.0"))
    bundle = build_discover_bundle(discovery, runtime_digests=gather_runtime_digests(target))
    catalog = bundle["tools_list"]
    subjects = bundle["tool_subjects"]
    if not isinstance(catalog, list) or not isinstance(subjects, dict):
        raise ValueError("discovery bundle has an invalid tool catalog")

    candidates = infer_all_candidates(analyze_catalog(catalog, subjects))
    evidence = EvidenceStore()
    run_count = int(os.environ.get("BABI_MODEL_RUNS", "2"))
    results = []
    for seed in range(run_count):
        model_env = dict(os.environ, BABI_PLANNER_SEED=str(seed))
        adapter = model_env.get("BABI_PLANNER_ADAPTER", "scripts/ollama_planner.py")
        model_name = model_env.get("BABI_PLANNER_MODEL", "gemma4:12b-mlx")
        planner = ModelPlanner(
            ModelPassSpec(
                command=(sys.executable, adapter),
                model=model_name,
                temperature=0.0,
                timeout=1000,
                env=model_env,
            ),
            evidence=evidence,
        )
        result = planner.run(
            server_subject=str(bundle["server_subject"]),
            tool_catalog=catalog,
            deterministic_candidates=candidates,
            sandbox=sandbox,
        )
        results.append(result)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"{target.name}-{seed}.json").write_bytes(canonical_json(result.envelope) + b"\n")

    print(f"model_runs={run_count}")
    print("steps=" + ",".join(str(len(result.plan.steps)) for result in results))
    if len(results) > 1:
        agreement = compare_plans(results[0].plan, results[1].plan)
        print(f"agreement={agreement.agreement_score}")
    print("plan_digests=" + ",".join(result.envelope_digest for result in results))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("--sandbox", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("runs/model-plans"))
    args = parser.parse_args()
    return asyncio.run(run(args.target, args.sandbox, args.output_dir))


if __name__ == "__main__":
    raise SystemExit(main())
