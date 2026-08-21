"""Corpus runner tests. The only live protocol round trip here goes
through the shared `tests/fixtures/fake_server.py` fixture (same pattern
as `tests/test_collect_stdio.py`) -- no real network or Docker calls.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from babi.sandbox.runner import (
    CorpusRunDenominator,
    RunnerConfig,
    TargetRunOutcome,
    ToolPlanStatus,
    run_targets,
    valid_first_call_rate,
)
from babi.target import TargetSpec

FIXTURE_SERVER = Path(__file__).parent.parent / "fixtures" / "fake_server.py"


def _fixture_target(name: str = "fake", *, env_vars: list[str] | None = None) -> TargetSpec:
    return TargetSpec(
        name=name,
        transport="stdio",
        command=[sys.executable, str(FIXTURE_SERVER)],
        args=[],
        env_vars=env_vars or [],
        network="bridge",
        package_or_endpoint="fake-fixture-server",
    )


def _broken_target(name: str = "broken") -> TargetSpec:
    return TargetSpec(
        name=name,
        transport="stdio",
        command=["babi-sandbox-test-binary-that-does-not-exist"],
        args=[],
        env_vars=[],
        network="none",
        package_or_endpoint="nonexistent",
    )


async def test_discovered_target_produces_a_plan_for_every_tool() -> None:
    summary = await run_targets([_fixture_target()])
    assert len(summary.targets) == 1
    result = summary.targets[0]
    assert result.outcome is TargetRunOutcome.DISCOVERED
    assert result.detail is None
    assert {plan.tool_name for plan in result.tool_plans} == {"ping", "mirror"}
    assert all(plan.status is ToolPlanStatus.SUCCESS for plan in result.tool_plans)
    assert all(plan.tool_subject.startswith("sha256:") for plan in result.tool_plans)


async def test_missing_credential_target_is_credentials_unavailable_not_discover_failed() -> None:
    summary = await run_targets([_fixture_target(env_vars=["BABI_SANDBOX_TEST_NONEXISTENT_TOKEN"])])
    result = summary.targets[0]
    assert result.outcome is TargetRunOutcome.CREDENTIALS_UNAVAILABLE
    assert "BABI_SANDBOX_TEST_NONEXISTENT_TOKEN" in (result.detail or "")
    assert result.tool_plans == []


async def test_present_credential_target_still_discovers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BABI_SANDBOX_TEST_PRESENT_TOKEN", "irrelevant-value")
    summary = await run_targets([_fixture_target(env_vars=["BABI_SANDBOX_TEST_PRESENT_TOKEN"])])
    assert summary.targets[0].outcome is TargetRunOutcome.DISCOVERED


async def test_broken_command_is_discover_failed_with_redacted_detail() -> None:
    summary = await run_targets([_broken_target()])
    result = summary.targets[0]
    assert result.outcome is TargetRunOutcome.DISCOVER_FAILED
    assert result.detail is not None and "FileNotFoundError" in result.detail


async def test_timeout_bounds_a_hanging_target() -> None:
    hang_target = TargetSpec(
        name="hangs",
        transport="stdio",
        command=[sys.executable, "-c", "import time; time.sleep(120)"],
        args=[],
        env_vars=[],
        network="none",
        package_or_endpoint="hang-fixture",
    )
    start = time.monotonic()
    summary = await run_targets([hang_target], config=RunnerConfig(max_concurrency=1, per_target_timeout_seconds=2))
    elapsed = time.monotonic() - start
    assert summary.targets[0].outcome is TargetRunOutcome.DISCOVER_FAILED
    assert elapsed < 30, "per_target_timeout_seconds must bound total runtime, not the full sleep duration"


async def test_bounded_concurrency_runs_every_target_regardless_of_limit() -> None:
    targets = [_fixture_target(name=f"fake-{i}") for i in range(5)]
    summary = await run_targets(targets, config=RunnerConfig(max_concurrency=2, per_target_timeout_seconds=30))
    assert len(summary.targets) == 5
    assert all(result.outcome is TargetRunOutcome.DISCOVERED for result in summary.targets)


async def test_results_preserve_input_order() -> None:
    targets = [_fixture_target(name=f"fake-{i}") for i in range(4)]
    summary = await run_targets(targets, config=RunnerConfig(max_concurrency=4))
    assert [result.target_name for result in summary.targets] == [t.name for t in targets]


async def test_denominator_math_across_mixed_outcomes() -> None:
    summary = await run_targets(
        [_fixture_target(), _fixture_target(env_vars=["BABI_SANDBOX_TEST_NONEXISTENT_TOKEN"]), _broken_target()],
        config=RunnerConfig(max_concurrency=3, per_target_timeout_seconds=15),
    )
    denominator = summary.denominator()
    assert denominator.targets_total == 3
    assert denominator.targets_discovered == 1
    assert denominator.targets_credentials_unavailable == 1
    assert denominator.targets_discover_failed == 1
    assert denominator.tools_total == 2
    assert denominator.tools_eligible == 2
    assert denominator.tools_success == 2
    assert denominator.tools_invalid == 0
    assert denominator.tools_inconclusive == 0
    assert valid_first_call_rate(denominator) == 1.0


def test_denominator_arithmetic_invariant_is_enforced_at_construction() -> None:
    with pytest.raises(ValidationError, match="targets_total"):
        CorpusRunDenominator(
            targets_total=5,
            targets_discovered=1,
            targets_credentials_unavailable=1,
            targets_discover_failed=1,  # sums to 3, not 5
            tools_total=0,
            tools_eligible=0,
            tools_success=0,
            tools_invalid=0,
            tools_inconclusive=0,
        )

    with pytest.raises(ValidationError, match="tools_eligible"):
        CorpusRunDenominator(
            targets_total=0,
            targets_discovered=0,
            targets_credentials_unavailable=0,
            targets_discover_failed=0,
            tools_total=10,
            tools_eligible=10,
            tools_success=1,
            tools_invalid=1,  # 1 + 1 = 2, not 10
            tools_inconclusive=0,
        )

    with pytest.raises(ValidationError, match="tools_success"):
        CorpusRunDenominator(
            targets_total=0,
            targets_discovered=0,
            targets_credentials_unavailable=0,
            targets_discover_failed=0,
            tools_total=5,
            tools_eligible=5,
            tools_success=1,
            tools_invalid=1,  # 1 + 1 = 2, not tools_eligible=5
            tools_inconclusive=0,
        )


def test_denominator_negative_counts_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        CorpusRunDenominator(
            targets_total=-1,
            targets_discovered=0,
            targets_credentials_unavailable=0,
            targets_discover_failed=0,
            tools_total=0,
            tools_eligible=0,
            tools_success=0,
            tools_invalid=0,
            tools_inconclusive=0,
        )


def test_valid_first_call_rate_is_none_without_eligible_tools() -> None:
    denominator = CorpusRunDenominator(
        targets_total=1,
        targets_discovered=0,
        targets_credentials_unavailable=1,
        targets_discover_failed=0,
        tools_total=0,
        tools_eligible=0,
        tools_success=0,
        tools_invalid=0,
        tools_inconclusive=0,
    )
    assert valid_first_call_rate(denominator) is None


def test_empty_target_list_produces_an_all_zero_denominator() -> None:
    denominator = CorpusRunDenominator(
        targets_total=0,
        targets_discovered=0,
        targets_credentials_unavailable=0,
        targets_discover_failed=0,
        tools_total=0,
        tools_eligible=0,
        tools_success=0,
        tools_invalid=0,
        tools_inconclusive=0,
    )
    assert valid_first_call_rate(denominator) is None
