#!/usr/bin/env python3
"""ModelPlanner command adapter for a local Ollama JSON response."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    prompt = sys.stdin.read()
    if not prompt:
        print("planner prompt is empty", file=sys.stderr)
        return 2

    endpoint = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    request = urllib.request.Request(
        f"{endpoint}/api/generate",
        data=json.dumps(
            {
                "model": os.environ.get("BABI_PLANNER_MODEL", "gemma4:12b-mlx"),
                "prompt": prompt,
                "format": "json",
                "stream": False,
                "options": {"temperature": 0, "seed": int(os.environ.get("BABI_PLANNER_SEED", "0"))},
            }
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        timeout = float(os.environ.get("BABI_OLLAMA_TIMEOUT", "600"))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        print(f"Ollama planner request failed: {error}", file=sys.stderr)
        return 1

    model_response = payload.get("response")
    if not isinstance(model_response, str):
        print("Ollama response lacks a string 'response' field", file=sys.stderr)
        return 1
    sys.stdout.write(model_response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
