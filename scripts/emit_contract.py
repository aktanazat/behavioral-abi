#!/usr/bin/env python3
"""Emit an in-toto behavioral contract from a reproduction artifact."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from mcp import types

from babi.canon import canonical_json
from babi.classify import BehaviorClassification
from babi.collect import build_discover_bundle, discover, gather_runtime_digests
from babi.contract import ContractEmitter
from babi.evidence import EvidenceStore
from babi.target import TargetSpec


async def run(target_path: Path, reproduction_path: Path, output: Path) -> int:
    target = TargetSpec.model_validate_json(target_path.read_text())
    reproduction = json.loads(reproduction_path.read_text())
    classification = BehaviorClassification.model_validate(reproduction["aggregate"])
    evidence = EvidenceStore()
    reproduction_digest = evidence.put_json(reproduction)

    discovery = await discover(target, client_info=types.Implementation(name="babi", version="0.1.0"))
    bundle = build_discover_bundle(discovery, runtime_digests=gather_runtime_digests(target))
    statement = ContractEmitter(evidence).emit(
        classification,
        bundle,
        list(classification.history_digests),
        reproduction_digest,
    )
    statement_digest = evidence.put_json(statement)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_json(statement) + b"\n")
    print(f"contract_digest={statement_digest}")
    print(f"history_count={len(classification.history_digests)}")
    print(f"output={output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("reproduction", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return asyncio.run(run(args.target, args.reproduction, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
