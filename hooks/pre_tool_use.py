#!/usr/bin/env python3
"""Claude Code `PreToolUse` command hook.

Reads the event JSON Claude Code writes to stdin, resolves the invoked
tool's exact `tool_subject` digest from hook configuration (never guessed
from `tool_name`), queries the behavioral-abi registry's retry policy over
HTTP, and prints Claude Code's `hookSpecificOutput` decision JSON to
stdout. Always exits 0 with valid JSON -- a missing config, an
unresolvable subject, or a registry that can't be reached all fail closed
to a neutral "ask", never a silent allow and never an uncaught exception
that would let Claude Code fall through to unmediated default behavior.

Configure via `BABI_HOOK_CONFIG` (path to a JSON file; see
`babi.hook.load_hook_config` for its shape) or `hooks/pre_tool_use.config.json`
next to this script. This script never writes to `~/.claude` itself --
wiring it into `.claude/settings.json` as a `PreToolUse` command hook is a
separate, deliberate deployment step.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_REPO_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

from babi.hook import HookConfig, HookConfigError, load_hook_config, map_policy_decision, neutral_output  # noqa: E402


def query_policy(config: HookConfig, tool_subject: str) -> dict[str, object]:
    url = (
        f"{config.registry_url.rstrip('/')}/v0/subjects/"
        f"{urllib.parse.quote(tool_subject, safe='')}/policy"
        f"?intent={urllib.parse.quote(config.intent, safe='')}"
    )
    request = urllib.request.Request(url, method="GET")  # noqa: S310 -- fixed http(s) URL from local hook config
    with urllib.request.urlopen(request, timeout=config.timeout) as response:  # noqa: S310
        body = response.read()
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("registry policy response was not a JSON object")
    return parsed


def run(raw_stdin: str, *, config_path: str | None) -> dict[str, object]:
    try:
        event = json.loads(raw_stdin) if raw_stdin.strip() else {}
    except json.JSONDecodeError:
        return neutral_output("PreToolUse hook received unparseable stdin JSON")
    if not isinstance(event, dict):
        return neutral_output("PreToolUse hook received a non-object stdin payload")

    tool_name = event.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        return neutral_output("PreToolUse hook received no tool_name")

    try:
        config = load_hook_config(config_path)
    except HookConfigError as error:
        return neutral_output(f"could not load hook config: {error}")

    tool_subject = config.subjects.get(tool_name)
    if tool_subject is None:
        return neutral_output(f"no exact subject digest configured for tool_name {tool_name!r}")

    try:
        policy = query_policy(config, tool_subject)
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
        return neutral_output(f"registry policy lookup failed: {error}")

    decision = policy.get("decision")
    if not isinstance(decision, str):
        return neutral_output("registry returned no decision")
    reason = policy.get("reason")
    reason = reason if isinstance(reason, str) and reason else "no reason returned by registry"
    contract_digest = policy.get("contract_digest")
    contract_digest = contract_digest if isinstance(contract_digest, str) else None

    return map_policy_decision(decision, reason=reason, contract_digest=contract_digest)


def main() -> int:
    output = run(sys.stdin.read(), config_path=os.environ.get("BABI_HOOK_CONFIG"))
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
