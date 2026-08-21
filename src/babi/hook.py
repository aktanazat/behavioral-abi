"""Claude Code `PreToolUse` decision mapping -- pure functions from a
registry policy decision to the `hookSpecificOutput` JSON contract
(https://code.claude.com/docs/en/hooks). `hooks/pre_tool_use.py` is the
executable command hook that wires this to stdin/stdout and an HTTP call
to the registry; nothing here does I/O, and nothing here branches on a
tool or operation name.

The exact `tool_subject` digest for the currently invoked tool is supplied
by hook *configuration* (`HookConfig.subjects`, an exact `tool_name ->
tool_subject` mapping populated ahead of time from a real DISCOVER run),
never guessed from `tool_name` -- Claude Code's `PreToolUse` input carries
only `tool_name`/`tool_input`, never the tool's full declared schema, so
there is no way to *compute* an exact subject digest inside the hook
itself, and RUBRIC.md's registry policy is exact-digest-only, "no fuzzy
names."
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from babi.canon import Json

DEFAULT_INTENT = "retry"
DEFAULT_TIMEOUT = 5.0
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "hooks" / "pre_tool_use.config.json"

# The only registry decision this hook ever treats as a positive block.
# Every other decision (including a registry "allow") stays neutral: this
# hook has evidence to withhold trust with, not authority to grant it.
_DENY_DECISIONS = frozenset({"deny_retry"})


class HookConfigError(ValueError):
    """Hook configuration is missing, unreadable, or malformed."""


@dataclass(frozen=True, slots=True)
class HookConfig:
    registry_url: str
    subjects: dict[str, str]
    intent: str = DEFAULT_INTENT
    timeout: float = DEFAULT_TIMEOUT


def load_hook_config(path: str | Path | None) -> HookConfig:
    """Load hook configuration from `path` (or `DEFAULT_CONFIG_PATH` if
    `None`/falsy). This is the *only* place a `tool_name -> tool_subject`
    mapping may come from; nothing in this module ever computes one."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    try:
        raw_text = config_path.read_text()
    except FileNotFoundError as error:
        raise HookConfigError(f"hook config not found: {config_path}") from error
    except OSError as error:
        raise HookConfigError(f"hook config unreadable: {config_path} ({error})") from error

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as error:
        raise HookConfigError(f"hook config is not valid JSON: {config_path} ({error})") from error
    if not isinstance(raw, dict):
        raise HookConfigError(f"hook config must be a JSON object: {config_path}")

    registry_url = raw.get("registry_url")
    if not isinstance(registry_url, str) or not registry_url:
        raise HookConfigError("hook config missing non-empty 'registry_url'")

    subjects = raw.get("subjects", {})
    if not isinstance(subjects, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in subjects.items()
    ):
        raise HookConfigError("hook config 'subjects' must be an object mapping tool_name -> tool_subject digest")

    intent = raw.get("intent", DEFAULT_INTENT)
    if not isinstance(intent, str) or not intent:
        raise HookConfigError("hook config 'intent' must be a non-empty string")

    timeout = raw.get("timeout", DEFAULT_TIMEOUT)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise HookConfigError("hook config 'timeout' must be a positive number")

    return HookConfig(registry_url=registry_url, subjects=dict(subjects), intent=intent, timeout=float(timeout))


def neutral_output(reason: str) -> dict[str, Json]:
    """This hook's abstain response: `permissionDecision: "ask"`, never
    `"allow"`. Used whenever this hook cannot make a positive
    determination -- missing/unreadable config, an unresolvable subject,
    an unreachable registry, or a registry decision this hook doesn't
    treat as a hard block."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }


def map_policy_decision(policy_decision: str, *, reason: str, contract_digest: str | None) -> dict[str, Json]:
    """Map one registry policy `decision` to Claude Code's `PreToolUse`
    `hookSpecificOutput`. `deny_retry` is the only decision that ever
    produces `permissionDecision: "deny"`; every other decision --
    `require_read_back`, `require_approval`, a registry `allow`, or
    `unknown` -- stays at `"ask"`, deferring to whatever permission
    handling the user already has configured."""
    output: dict[str, Json] = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny" if policy_decision in _DENY_DECISIONS else "ask",
            "permissionDecisionReason": reason,
        }
    }
    if contract_digest is not None:
        output["hookSpecificOutput"]["additionalContext"] = f"behavioral-abi contract: {contract_digest}"
    return output


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_INTENT",
    "DEFAULT_TIMEOUT",
    "HookConfig",
    "HookConfigError",
    "load_hook_config",
    "map_policy_decision",
    "neutral_output",
]
