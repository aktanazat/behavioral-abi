"""Claude Code PreToolUse hook: `babi.hook`'s pure decision-mapping
functions unit-tested directly, plus `hooks/pre_tool_use.py` exercised as
a real subprocess against a real registry HTTP server for the "hook deny
path" acceptance criterion -- the exact deployment mechanism Claude Code
uses (stdin JSON in, `hookSpecificOutput` JSON out)."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from babi.evidence import EvidenceStore
from babi.hook import (
    DEFAULT_INTENT,
    DEFAULT_TIMEOUT,
    HookConfig,
    HookConfigError,
    load_hook_config,
    map_policy_decision,
    neutral_output,
)
from babi.registry.http import make_server
from babi.registry.store import Registry

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "hooks" / "pre_tool_use.py"

TOOL_SUBJECT = "sha256:" + "ab" * 32
TOOL_NAME = "mcp__widgets__create_widget"


# -- load_hook_config -------------------------------------------------------------


def test_load_hook_config_happy_path(tmp_path: Path) -> None:
    config_path = tmp_path / "hook_config.json"
    config_path.write_text(
        json.dumps(
            {
                "registry_url": "http://127.0.0.1:9",
                "subjects": {TOOL_NAME: TOOL_SUBJECT},
                "intent": "retry",
                "timeout": 3.5,
            }
        )
    )
    config = load_hook_config(str(config_path))
    assert config == HookConfig(
        registry_url="http://127.0.0.1:9", subjects={TOOL_NAME: TOOL_SUBJECT}, intent="retry", timeout=3.5
    )


def test_load_hook_config_defaults_intent_and_timeout(tmp_path: Path) -> None:
    config_path = tmp_path / "hook_config.json"
    config_path.write_text(json.dumps({"registry_url": "http://x", "subjects": {}}))
    config = load_hook_config(str(config_path))
    assert config.intent == DEFAULT_INTENT
    assert config.timeout == DEFAULT_TIMEOUT


def test_load_hook_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(HookConfigError, match="not found"):
        load_hook_config(str(tmp_path / "does-not-exist.json"))


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        json.dumps({"subjects": {}}),  # missing registry_url
        json.dumps({"registry_url": ""}),  # empty registry_url
        json.dumps({"registry_url": "http://x", "subjects": {"a": 5}}),  # non-string value
        json.dumps({"registry_url": "http://x", "subjects": "not-a-dict"}),
        json.dumps({"registry_url": "http://x", "subjects": {}, "intent": ""}),
        json.dumps({"registry_url": "http://x", "subjects": {}, "timeout": 0}),
        json.dumps({"registry_url": "http://x", "subjects": {}, "timeout": -1}),
        json.dumps({"registry_url": "http://x", "subjects": {}, "timeout": "5"}),
        json.dumps({"registry_url": "http://x", "subjects": {}, "timeout": True}),
    ],
)
def test_load_hook_config_rejects_malformed_config(tmp_path: Path, raw: str) -> None:
    config_path = tmp_path / "hook_config.json"
    config_path.write_text(raw)
    with pytest.raises(HookConfigError):
        load_hook_config(str(config_path))


def test_load_hook_config_none_path_uses_default_location() -> None:
    # No fixture at the real default location in this checkout -> fails closed, never crashes.
    with pytest.raises(HookConfigError):
        load_hook_config(None)


# -- neutral_output / map_policy_decision ------------------------------------------


def test_neutral_output_is_ask_never_allow() -> None:
    output = neutral_output("some reason")
    assert output == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": "some reason",
        }
    }


def test_map_policy_decision_deny_retry_maps_to_deny() -> None:
    output = map_policy_decision("deny_retry", reason="unsafe to retry", contract_digest="sha256:" + "11" * 32)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert output["hookSpecificOutput"]["permissionDecisionReason"] == "unsafe to retry"
    assert output["hookSpecificOutput"]["additionalContext"] == f"behavioral-abi contract: sha256:{'11' * 32}"


@pytest.mark.parametrize("decision", ["unknown", "allow", "require_read_back", "require_approval", "anything-else"])
def test_map_policy_decision_everything_but_deny_retry_stays_ask_never_allow(decision: str) -> None:
    output = map_policy_decision(decision, reason="r", contract_digest=None)
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert output["hookSpecificOutput"]["permissionDecision"] != "allow"


def test_map_policy_decision_omits_additional_context_when_no_contract_digest() -> None:
    output = map_policy_decision("deny_retry", reason="r", contract_digest=None)
    assert "additionalContext" not in output["hookSpecificOutput"]


def test_hook_specific_output_always_names_pretooluse() -> None:
    for decision in ("deny_retry", "unknown", "allow"):
        output = map_policy_decision(decision, reason="r", contract_digest=None)
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


# -- integration: real subprocess against a real registry HTTP server ---------------


@pytest.fixture
def live_registry(tmp_path: Path) -> Iterator[tuple[str, Registry]]:
    registry = Registry(tmp_path / "registry.sqlite3", EvidenceStore(tmp_path / "evidence"))
    server = make_server(registry, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", registry
    finally:
        server.shutdown()
        thread.join(timeout=5)
        registry.close()


def _run_hook(stdin_payload: dict, *, config_path: Path | None) -> dict:
    env = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin"}
    if config_path is not None:
        env["BABI_HOOK_CONFIG"] = str(config_path)
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(stdin_payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    assert result.returncode == 0, f"hook exited {result.returncode}, stderr={result.stderr}"
    return json.loads(result.stdout)


def _write_hook_config(tmp_path: Path, registry_url: str, subjects: dict[str, str]) -> Path:
    path = tmp_path / "hook_config.json"
    path.write_text(json.dumps({"registry_url": registry_url, "subjects": subjects, "intent": "retry"}))
    return path


def _record_for(subject_digest: str) -> dict:
    return {
        "statement": {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": "mcp:widgets/create_widget", "digest": {"sha256": subject_digest.removeprefix("sha256:")}}],
            "predicateType": "https://behavioral-abi.dev/contract/v0",
            "predicate": {
                "operation": {
                    "protocol": "mcp", "name": "create_widget", "input_schema_digest": "sha256:" + "11" * 32,
                    "vendor_declared": {},
                },
                "observed": {"idempotent": False, "reversible": "unknown", "side_effects": [], "consistency": {"model": "unknown"}},
                "preconditions": [],
                "evidence_ref": {"uri": "cas://sha256/" + "ee" * 32, "sha256": "sha256:" + "ee" * 32},
                "verified_at": "2026-08-20T00:00:02Z",
                "prober": {"id": "behavioral-abi-prober", "version": "0.1.0-test"},
                "provenance": {
                    "observer_coverage": ["read_back"], "run_count": 1,
                    "clean_state": {"run_count": 1, "distinct_clean_state_ids": 1, "clean_state_ids": ["c1"]},
                    "protocol_revision": "2026-07-28", "package_or_endpoint": "@example/widgets-mcp",
                    "planner": {}, "confidence": {"method": "reproduction_count", "reproduced_runs": 2, "total_runs": 2, "value": 1.0},
                    "discrepancy_classes": [],
                    "claims": [{"property": "idempotent", "status": "observed_false", "runs": 2, "scope": "REPEAT", "detail": None, "evidence_digests": []}],
                },
            },
        },
        "dsse_envelope": {"payload": "e30=", "payloadType": "application/vnd.in-toto+json", "signatures": [{"sig": "c2ln"}]},
        "cosign_bundle": None,
    }


def test_hook_deny_path_end_to_end(tmp_path: Path, live_registry: tuple[str, Registry]) -> None:
    """RUBRIC.md pass condition 6: one PreToolUse hook consumes a
    verified contract and blocks an unsafe retry before execution."""
    base_url, registry = live_registry
    registry.insert_contract(_record_for(TOOL_SUBJECT))
    config_path = _write_hook_config(tmp_path, base_url, {TOOL_NAME: TOOL_SUBJECT})

    output = _run_hook(
        {"hook_event_name": "PreToolUse", "tool_name": TOOL_NAME, "tool_input": {"name": "x"}, "tool_use_id": "toolu_1"},
        config_path=config_path,
    )
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "unsafe" in output["hookSpecificOutput"]["permissionDecisionReason"] or "additional effect" in output["hookSpecificOutput"]["permissionDecisionReason"]
    assert output["hookSpecificOutput"]["additionalContext"].startswith("behavioral-abi contract: sha256:")


def test_hook_allow_path_never_emits_allow(tmp_path: Path, live_registry: tuple[str, Registry]) -> None:
    base_url, registry = live_registry
    record = _record_for(TOOL_SUBJECT)
    record["statement"]["predicate"]["observed"]["idempotent"] = True
    record["statement"]["predicate"]["provenance"]["claims"][0]["status"] = "observed_true"
    registry.insert_contract(record)
    config_path = _write_hook_config(tmp_path, base_url, {TOOL_NAME: TOOL_SUBJECT})

    output = _run_hook(
        {"hook_event_name": "PreToolUse", "tool_name": TOOL_NAME, "tool_input": {}},
        config_path=config_path,
    )
    # registry says "allow" for this subject, but the hook itself must still never say "allow"
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_hook_unconfigured_tool_name_is_neutral(tmp_path: Path, live_registry: tuple[str, Registry]) -> None:
    base_url, registry = live_registry
    registry.insert_contract(_record_for(TOOL_SUBJECT))
    config_path = _write_hook_config(tmp_path, base_url, {TOOL_NAME: TOOL_SUBJECT})

    output = _run_hook(
        {"hook_event_name": "PreToolUse", "tool_name": "mcp__other__unrelated_tool", "tool_input": {}},
        config_path=config_path,
    )
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "no exact subject digest configured" in output["hookSpecificOutput"]["permissionDecisionReason"]


def test_hook_unreachable_registry_is_neutral_not_a_crash(tmp_path: Path) -> None:
    config_path = _write_hook_config(tmp_path, "http://127.0.0.1:1", {TOOL_NAME: TOOL_SUBJECT})
    output = _run_hook(
        {"hook_event_name": "PreToolUse", "tool_name": TOOL_NAME, "tool_input": {}},
        config_path=config_path,
    )
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "registry policy lookup failed" in output["hookSpecificOutput"]["permissionDecisionReason"]


def test_hook_missing_config_is_neutral_not_a_crash(tmp_path: Path) -> None:
    output = _run_hook(
        {"hook_event_name": "PreToolUse", "tool_name": TOOL_NAME, "tool_input": {}},
        config_path=tmp_path / "does-not-exist.json",
    )
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "could not load hook config" in output["hookSpecificOutput"]["permissionDecisionReason"]


def test_hook_missing_tool_name_is_neutral(tmp_path: Path) -> None:
    config_path = _write_hook_config(tmp_path, "http://127.0.0.1:1", {})
    output = _run_hook({"hook_event_name": "PreToolUse", "tool_input": {}}, config_path=config_path)
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "no tool_name" in output["hookSpecificOutput"]["permissionDecisionReason"]


def test_hook_malformed_stdin_is_neutral(tmp_path: Path) -> None:
    config_path = _write_hook_config(tmp_path, "http://127.0.0.1:1", {})
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input="not json {{{",
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "BABI_HOOK_CONFIG": str(config_path)},
        timeout=15,
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_hook_script_is_executable() -> None:
    assert HOOK_SCRIPT.is_file()
    import stat

    mode = HOOK_SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR
