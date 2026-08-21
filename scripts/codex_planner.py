#!/usr/bin/env python3
"""ModelPlanner command adapter for an ephemeral read-only Codex run."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

def strict_schema(value):
    if isinstance(value, dict):
        normalized = {key: strict_schema(item) for key, item in value.items()}
        properties = normalized.get("properties")
        if isinstance(properties, dict):
            normalized["required"] = list(properties)
            normalized["additionalProperties"] = False
        return normalized
    if isinstance(value, list):
        return [strict_schema(item) for item in value]
    return value


def main() -> int:
    prompt = sys.stdin.read()
    if not prompt:
        print("planner prompt is empty", file=sys.stderr)
        return 2

    context_start = prompt.find("{")
    if context_start < 0:
        print("planner prompt lacks its JSON context", file=sys.stderr)
        return 2
    try:
        context = json.loads(prompt[context_start:])
        response_schema = context["response_json_schema"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        print(f"planner prompt has invalid response schema: {error}", file=sys.stderr)
        return 2

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as schema_file:
        json.dump(strict_schema(response_schema), schema_file)
        schema_args = (
            ["--output-schema", schema_file.name]
            if os.environ.get("BABI_CODEX_STRICT", "1") != "0"
            else []
        )
        command = [
            "codex",
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "-C",
            "/tmp",
            "-m",
            os.environ.get("BABI_CODEX_MODEL", "gpt-5.3-codex-spark"),
            *schema_args,
            "-",
        ]
        timeout = float(os.environ.get("BABI_CODEX_TIMEOUT", "900"))
        completed = subprocess.run(command, input=prompt, text=True, capture_output=True, timeout=timeout, check=False)
    if completed.returncode != 0:
        print(completed.stderr[-4000:], file=sys.stderr)
        return completed.returncode
    sys.stdout.write(completed.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
