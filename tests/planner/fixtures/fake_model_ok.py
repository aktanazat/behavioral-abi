"""Fake constrained-model-pass command: reads the planner's prompt from
stdin, pulls the real server/tool subjects out of the embedded JSON
context, and prints one syntactically valid `ProbePlan` JSON object
referencing them. Used only to exercise `ModelPlanner` without any real
model provider.
"""

from __future__ import annotations

import json
import sys


def main() -> None:
    prompt = sys.stdin.read()
    try:
        context = json.loads(prompt[prompt.index("{") :])
    except (ValueError, json.JSONDecodeError):
        context = {}

    server_subject = context.get("server_subject") or ("sha256:" + "0" * 64)
    tool_subject = "sha256:" + "1" * 64
    candidates = context.get("deterministic_candidates") or {}
    catalog_entries = candidates.get("catalog") or []
    if catalog_entries:
        tool_subject = catalog_entries[0]["tool_subject"]

    plan = {
        "subject_digest": server_subject,
        "cleanup_budget": 0,
        "steps": [
            {"id": "discover", "primitive": "DISCOVER", "tool_subject": server_subject},
            {
                "id": "call",
                "primitive": "CALL_ONCE",
                "tool_subject": tool_subject,
                "depends_on": ["discover"],
            },
        ],
        "rationale_evidence": [
            {
                "edge": {"from_step": "discover", "to_step": "call"},
                "provenance": "model-pass",
                "detail": "the response command preserved the catalog dependency",
            }
        ],
    }
    sys.stdout.write(json.dumps(plan))


if __name__ == "__main__":
    main()
