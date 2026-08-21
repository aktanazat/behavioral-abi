#!/usr/bin/env python3
"""Export report-referenced CAS objects into a portable evidence bundle."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from babi.evidence import EvidenceStore

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def report_evidence_digests(report: dict) -> set[str]:
    candidates: list[object] = []
    population = report.get("population")
    if isinstance(population, dict):
        candidates.append(population.get("evidence_digest"))
    rubric = report.get("rubric")
    if isinstance(rubric, dict):
        for group_name in ("pass_conditions", "kill_conditions"):
            group = rubric.get(group_name)
            if isinstance(group, list):
                for condition in group:
                    if isinstance(condition, dict):
                        values = condition.get("evidence_digests")
                        if isinstance(values, list):
                            candidates.extend(values)
    reproducers = report.get("reproducers")
    if isinstance(reproducers, list):
        for reproducer in reproducers:
            if not isinstance(reproducer, dict):
                continue
            candidates.append(reproducer.get("reproducer_digest"))
            for field in ("history_digests", "evidence_digests"):
                values = reproducer.get(field)
                if isinstance(values, list):
                    candidates.extend(values)
    return {value for value in candidates if isinstance(value, str) and _DIGEST.fullmatch(value)}


def main() -> int:
    report = json.loads(Path("report/REPORT.json").read_text())
    if not isinstance(report, dict):
        raise ValueError("report/REPORT.json must contain an object")
    source = EvidenceStore()
    destination = Path("runs/evidence/sha256")
    exported: list[str] = []
    missing: list[str] = []
    for digest in sorted(report_evidence_digests(report)):
        source_path = source.path(digest)
        if not source_path.exists():
            missing.append(digest)
            continue
        bare = digest.removeprefix("sha256:")
        target = destination / bare[:2] / bare
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target)
        exported.append(digest)

    index = {"kind": "report-evidence-export/v0", "exported": exported, "missing": missing}
    index_path = Path("runs/evidence/index.json")
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    print(f"exported={len(exported)} missing={len(missing)}")
    print(f"index={index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
