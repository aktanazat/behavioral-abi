"""Fake constrained-model-pass command that writes syntactically valid JSON
which does not validate as a `ProbePlan` (unknown primitive, wrong field
type) -- exercises `MalformedModelResponseError` on a schema failure
distinct from a JSON-syntax failure."""

from __future__ import annotations

import json
import sys


def main() -> None:
    sys.stdin.read()
    bogus = {
        "subject_digest": "sha256:" + "0" * 64,
        "cleanup_budget": 0,
        "steps": [{"id": "s", "primitive": "DELETE_EVERYTHING", "tool_subject": "sha256:" + "1" * 64}],
    }
    sys.stdout.write(json.dumps(bogus))


if __name__ == "__main__":
    main()
