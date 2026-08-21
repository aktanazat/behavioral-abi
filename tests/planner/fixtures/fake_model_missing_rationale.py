"""Emit a schema-valid ProbePlan whose dependency lacks provenance."""

from __future__ import annotations

import json
import sys


def main() -> None:
    prompt = sys.stdin.read()
    context = json.loads(prompt[prompt.index("{") :])
    candidates = context["deterministic_candidates"]["catalog"]
    plan = {
        "subject_digest": context["server_subject"],
        "cleanup_budget": 0,
        "steps": [
            {"id": "discover", "primitive": "DISCOVER", "tool_subject": context["server_subject"]},
            {
                "id": "call",
                "primitive": "CALL_ONCE",
                "tool_subject": candidates[0]["tool_subject"],
                "depends_on": ["discover"],
            },
        ],
    }
    sys.stdout.write(json.dumps(plan))


if __name__ == "__main__":
    main()
