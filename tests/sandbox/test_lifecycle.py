import sys

import pytest
from pydantic import ValidationError

from babi.sandbox.lifecycle import SandboxLifecycleAdapter, render_command, run_setup, run_teardown


def test_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        SandboxLifecycleAdapter.model_validate({"target_label": "x", "some_random_field": "y"})


def test_env_var_name_with_embedded_value_is_forbidden() -> None:
    # Mirrors TargetSpec/SandboxManifest: env_vars is names only, never
    # "NAME=value" -- the same EnvironmentVariableName type is reused here.
    with pytest.raises(ValidationError):
        SandboxLifecycleAdapter.model_validate({"target_label": "x", "env_vars": ["TOKEN=secret"]})


def test_default_timeout_is_positive() -> None:
    adapter = SandboxLifecycleAdapter(target_label="x")
    assert adapter.timeout_seconds > 0
    assert adapter.setup_command is None
    assert adapter.teardown_command is None
    assert adapter.env_vars == []


def test_render_command_fills_named_placeholders() -> None:
    rendered = render_command(["echo", "{resource_prefix}{run_id}"], {"resource_prefix": "abi-", "run_id": "r1"})
    assert rendered == ["echo", "abi-r1"]


def test_render_command_raises_on_unresolved_placeholder() -> None:
    with pytest.raises(KeyError):
        render_command(["echo", "{missing}"], {})


def test_render_command_is_noop_without_placeholders() -> None:
    assert render_command(["echo", "hello"], {}) == ["echo", "hello"]


def test_setup_and_teardown_are_honest_noops_when_no_command_declared() -> None:
    adapter = SandboxLifecycleAdapter(target_label="noop")
    setup_result = run_setup(adapter)
    teardown_result = run_teardown(adapter)
    assert setup_result.ran is False
    assert teardown_result.ran is False
    assert setup_result.error is None
    assert teardown_result.error is None


def test_setup_reports_missing_credentials_without_running_the_command() -> None:
    adapter = SandboxLifecycleAdapter(
        target_label="needs-cred",
        setup_command=[sys.executable, "-c", "import pathlib; pathlib.Path('should-not-exist').touch()"],
        env_vars=["BABI_SANDBOX_TEST_NONEXISTENT_VAR"],
    )
    result = run_setup(adapter)
    assert result.ran is False
    assert "BABI_SANDBOX_TEST_NONEXISTENT_VAR" in (result.error or "")


def test_successful_command_captures_exit_code_and_output() -> None:
    adapter = SandboxLifecycleAdapter(
        target_label="ok",
        setup_command=[sys.executable, "-c", "print('provisioned')"],
        timeout_seconds=10,
    )
    result = run_setup(adapter)
    assert result.ran is True
    assert result.exit_code == 0
    assert result.output is not None and "provisioned" in result.output
    assert result.error is None


def test_nonzero_exit_is_reported_not_raised() -> None:
    adapter = SandboxLifecycleAdapter(target_label="fails", setup_command=[sys.executable, "-c", "import sys; sys.exit(3)"])
    result = run_setup(adapter)
    assert result.ran is True
    assert result.exit_code == 3


def test_credential_values_are_redacted_from_captured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BABI_SANDBOX_TEST_SECRET", "shh-do-not-print-me")
    adapter = SandboxLifecycleAdapter(
        target_label="secretive",
        setup_command=[sys.executable, "-c", "import os,sys; print(os.environ['BABI_SANDBOX_TEST_SECRET']); sys.stderr.write(os.environ['BABI_SANDBOX_TEST_SECRET'])"],
        env_vars=["BABI_SANDBOX_TEST_SECRET"],
    )
    result = run_setup(adapter)
    assert result.ran is True and result.exit_code == 0
    assert "shh-do-not-print-me" not in (result.output or "")
    assert "<redacted>" in (result.output or "")


def test_timeout_is_enforced_and_reported() -> None:
    adapter = SandboxLifecycleAdapter(
        target_label="slow", setup_command=[sys.executable, "-c", "import time; time.sleep(30)"], timeout_seconds=1
    )
    result = run_setup(adapter)
    assert result.ran is True
    assert result.exit_code is None
    assert "timed out" in (result.error or "")
    assert result.duration_seconds < 10


def test_command_needing_path_lookup_still_resolves() -> None:
    # Regression: the executor must merge a safe base environment (PATH,
    # HOME, ...) with resolved credentials, not run with credentials-only
    # env -- otherwise any binary outside a POSIX default PATH (docker, gh,
    # uvx, ...) can never be found. Uses a bare name (not sys.executable,
    # which is already absolute and would bypass PATH lookup entirely).
    adapter = SandboxLifecycleAdapter(target_label="path-lookup", setup_command=["python3", "--version"])
    result = run_setup(adapter)
    assert result.ran is True
    assert result.exit_code == 0
    assert result.output is not None and "Python" in result.output
