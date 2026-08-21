#!/usr/bin/env python3
"""Load one signed contract record into registry v0 and query policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from babi.evidence import EvidenceStore
from babi.registry import Registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("statement", type=Path)
    parser.add_argument("envelope", type=Path)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--db", type=Path, default=Path("runs/registry.sqlite3"))
    args = parser.parse_args()

    record = {
        "statement": json.loads(args.statement.read_text()),
        "dsse_envelope": json.loads(args.envelope.read_text()),
        "cosign_bundle": json.loads(args.bundle.read_text()),
    }
    subject = "sha256:" + record["statement"]["subject"][0]["digest"]["sha256"]
    with Registry(args.db, EvidenceStore()) as registry:
        contract_digest = registry.insert_contract(record)
        latest = registry.latest(subject)
        policy = registry.policy(subject, intent="retry")
    print(f"subject={subject}")
    print(f"contract_digest={contract_digest}")
    print(f"rekor_log_index={latest.rekor_log_index if latest else None}")
    print(f"policy={policy.decision}")
    print(f"reason={policy.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
