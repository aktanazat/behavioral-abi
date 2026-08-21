"""Real, credential-free DISCOVER against a bounded, fast subset of the
top-20 corpus's actual targets/*.json files -- no mocks, no fixtures. This
is the corpus runner exercised against the real population it exists to
measure. Skips gracefully wherever Docker or the network is unavailable,
rather than failing on an environment gap unrelated to this code.

Heavier top-20 entries (Playwright, Chrome DevTools -- both need a working
Chromium) are deliberately excluded from this always-run test to keep it
fast; they were still attempted manually and their exact outcome is
reported in this slice's handoff notes. Only this fixed, explicit list of
files is loaded and run -- never the whole targets/ directory -- so one
slow/credentialed entry elsewhere in the corpus can never make this test
slower or flakier than the seven targets it actually checks.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from babi.sandbox.runner import RunnerConfig, TargetRunOutcome, run_targets
from babi.target import TargetSpec

REPO_ROOT = Path(__file__).parent.parent.parent
TARGETS_DIR = REPO_ROOT / "targets"

# Every one of these declares zero env_vars (verified by
# test_targets_top20.py::test_credential_free_targets_declare_no_env_vars)
# and is fast: no Chromium download, no large model/browser dependency.
FAST_CREDENTIAL_FREE_TARGET_NAMES = [
    "filesystem",
    "memory",
    "fetch",
    "sequential-thinking",
    "context7",
    "aws-documentation",
    "serena",
]


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _network_available() -> bool:
    try:
        result = subprocess.run(
            ["curl", "-sS", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}", "https://registry.npmjs.org/"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() == "200"
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(
    not (_docker_available() and _network_available()),
    reason="Docker and network access are required for real credential-free discovery",
)


def _load_target(name: str) -> TargetSpec:
    path = TARGETS_DIR / f"{name}.json"
    return TargetSpec.model_validate(json.loads(path.read_text()))


async def test_at_least_five_credential_free_top20_targets_discover_successfully() -> None:
    targets = [_load_target(name) for name in FAST_CREDENTIAL_FREE_TARGET_NAMES]
    assert all(t.env_vars == [] for t in targets), "this test only attempts genuinely credential-free targets"

    summary = await run_targets(targets, config=RunnerConfig(max_concurrency=4, per_target_timeout_seconds=90))
    results_by_name = {r.target_name: r for r in summary.targets}

    report_lines = [
        f"  {name}: {result.outcome.value}"
        + (f" ({result.detail})" if result.detail else f" [{len(result.tool_plans)} tools]")
        for name, result in sorted(results_by_name.items())
    ]
    print("\ncredential-free discovery report:\n" + "\n".join(report_lines))

    successes = [name for name, result in results_by_name.items() if result.outcome is TargetRunOutcome.DISCOVERED]
    failures = {
        name: result.detail for name, result in results_by_name.items() if result.outcome is not TargetRunOutcome.DISCOVERED
    }

    assert len(successes) >= 5, (
        f"expected >=5 successful credential-free discoveries, got {len(successes)}: {successes}; failures: {failures}"
    )
