"""Generic sandbox lifecycle abstraction: setup/teardown as typed
environment-adapter config, never as tool/operation-keyed code.

A `SandboxLifecycleAdapter` describes *how* to provision and tear down one
disposable sandbox environment: a subprocess command template, the
env-var *names* it needs (never values, mirroring `TargetSpec.env_vars`),
and a timeout. `run_setup`/`run_teardown` are the single generic executor
every sandbox uses -- nothing here branches on which sandbox is running.
Per-sandbox specifics (the `gh` CLI vs `curl` vs `docker`) live entirely in
the adapter's *data* (`sandboxes/lifecycle/*.json`), never in a Python
branch keyed by name (RUBRIC.md's "Allowed": "Generic environment adapters
for a local process sandbox, disposable remote account").
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from babi.sandbox.credentials import MissingCredentialsError, redact, require_credentials
from babi.target import EnvironmentVariableName

DEFAULT_LIFECYCLE_DIR = Path("sandboxes") / "lifecycle"

# Mirrors babi.collect.discover's DEFAULT_INHERITED_ENV_VARS-equivalent
# allowlist: enough for the spawned command to locate its own binaries
# (docker, gh, curl, uvx, ...) and behave sanely, never a path into leaking
# arbitrary ambient state. Credentials only ever join this set through
# `adapter.env_vars`, resolved by name.
_BASE_ENVIRONMENT_NAMES = ("HOME", "LANG", "LC_ALL", "PATH", "TMPDIR")


class SandboxLifecycleAdapter(BaseModel):
    """Typed environment-adapter config for one sandbox's setup/teardown.
    `extra="forbid"` closes the field set for the same reason
    `SandboxManifest`'s does: only rubric-allowed, generic lifecycle shape
    is representable here -- there is no field an operation-specific value
    could hide in."""

    model_config = ConfigDict(extra="forbid")

    target_label: str
    setup_command: list[str] | None = None
    teardown_command: list[str] | None = None
    env_vars: list[EnvironmentVariableName] = Field(default_factory=list)
    timeout_seconds: float = Field(default=60.0, gt=0)


class LifecycleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["setup", "teardown"]
    ran: bool
    exit_code: int | None = None
    duration_seconds: float
    output: str | None = None
    error: str | None = None


def render_command(command: list[str], context: dict[str, str]) -> list[str]:
    """Fill `{name}`-style placeholders in each argument from `context`
    (e.g. a manifest's `resource_prefix`, or a generated `run_id`) --
    never a credential; those only ever reach a command through
    `env_vars`. A referenced name absent from `context` raises `KeyError`
    naming it: a template that can't be filled is a config error, not a
    command to run half-substituted."""
    return [arg.format(**context) for arg in command]


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def _run(
    adapter: SandboxLifecycleAdapter,
    command: list[str] | None,
    *,
    phase: Literal["setup", "teardown"],
    context: dict[str, str],
) -> LifecycleResult:
    if not command:
        return LifecycleResult(phase=phase, ran=False, duration_seconds=0.0)

    try:
        resolved_credentials = require_credentials(adapter.env_vars)
    except MissingCredentialsError as error:
        return LifecycleResult(phase=phase, ran=False, duration_seconds=0.0, error=str(error))

    resolved_env = {
        **{name: value for name in _BASE_ENVIRONMENT_NAMES if (value := os.environ.get(name)) is not None},
        **resolved_credentials,
    }
    secrets = list(resolved_credentials.values())
    try:
        rendered = render_command(command, context)
    except KeyError as error:
        return LifecycleResult(phase=phase, ran=False, duration_seconds=0.0, error=f"unresolved command placeholder: {error}")

    started = time.monotonic()
    try:
        completed = subprocess.run(
            rendered,
            env=resolved_env,
            capture_output=True,
            text=True,
            timeout=adapter.timeout_seconds,
            check=False,
        )
        duration = time.monotonic() - started
        combined_output = redact(_decode(completed.stdout) + _decode(completed.stderr), secrets)
        return LifecycleResult(
            phase=phase,
            ran=True,
            exit_code=completed.returncode,
            duration_seconds=duration,
            output=combined_output or None,
        )
    except subprocess.TimeoutExpired as error:
        duration = time.monotonic() - started
        partial_output = redact(_decode(error.stdout) + _decode(error.stderr), secrets)
        return LifecycleResult(
            phase=phase,
            ran=True,
            duration_seconds=duration,
            output=partial_output or None,
            error=f"timed out after {adapter.timeout_seconds}s",
        )
    except OSError as error:
        duration = time.monotonic() - started
        return LifecycleResult(phase=phase, ran=True, duration_seconds=duration, error=redact(str(error), secrets))


def run_setup(adapter: SandboxLifecycleAdapter, *, context: dict[str, str] | None = None) -> LifecycleResult:
    """Run `adapter.setup_command`, or report `ran=False` if the adapter
    declares none -- a sandbox with nothing to provision (e.g. a
    feature-complete test mode) is a legitimate, honest no-op, not a
    failure."""
    return _run(adapter, adapter.setup_command, phase="setup", context=context or {})


def run_teardown(adapter: SandboxLifecycleAdapter, *, context: dict[str, str] | None = None) -> LifecycleResult:
    """Run `adapter.teardown_command`, or report `ran=False` if the
    adapter declares none."""
    return _run(adapter, adapter.teardown_command, phase="teardown", context=context or {})


def load_lifecycle_adapter(path: Path | str) -> SandboxLifecycleAdapter:
    raw = json.loads(Path(path).read_text())
    return SandboxLifecycleAdapter.model_validate(raw)


__all__ = [
    "DEFAULT_LIFECYCLE_DIR",
    "LifecycleResult",
    "SandboxLifecycleAdapter",
    "load_lifecycle_adapter",
    "render_command",
    "run_setup",
    "run_teardown",
]
