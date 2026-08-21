"""Real, end-to-end proof that the generic sandbox lifecycle abstraction
works: provision a disposable local Postgres, DISCOVER the top-20
corpus's postgres-reference target against it, tear it down. No shortcuts
-- this is the actual `docker`/`npx` path a real corpus run takes, not a
mock. Skips gracefully wherever Docker isn't available (e.g. CI without
Docker-in-Docker), rather than failing the whole suite on an environment
gap unrelated to this code's correctness.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from babi.sandbox.lifecycle import load_lifecycle_adapter, run_setup, run_teardown
from babi.sandbox.runner import RunnerConfig, TargetRunOutcome, ToolPlanStatus, run_targets
from babi.target import TargetSpec

REPO_ROOT = Path(__file__).parent.parent.parent
ADAPTER_PATH = REPO_ROOT / "sandboxes" / "lifecycle" / "postgres-reference.json"
TARGET_PATH = REPO_ROOT / "targets" / "postgres-reference.json"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="Docker is required for the local Postgres sandbox lifecycle")


async def test_postgres_sandbox_lifecycle_setup_discover_teardown() -> None:
    adapter = load_lifecycle_adapter(ADAPTER_PATH)
    target = TargetSpec.model_validate(json.loads(TARGET_PATH.read_text()))

    setup_result = run_setup(adapter)
    try:
        assert setup_result.ran is True
        assert setup_result.exit_code == 0, setup_result.error or setup_result.output

        summary = await run_targets([target], config=RunnerConfig(max_concurrency=1, per_target_timeout_seconds=30))
        result = summary.targets[0]
        assert result.outcome is TargetRunOutcome.DISCOVERED, result.detail
        assert len(result.tool_plans) >= 1
        assert any(plan.status is ToolPlanStatus.SUCCESS for plan in result.tool_plans)

        denominator = summary.denominator()
        assert denominator.targets_discovered == 1
        assert denominator.tools_eligible >= 1
    finally:
        teardown_result = run_teardown(adapter)
        assert teardown_result.ran is True
        assert teardown_result.exit_code == 0
