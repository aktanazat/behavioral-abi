"""Run deterministic first-call checks across the frozen corpus."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import datetime, timezone
from enum import Enum
from importlib.metadata import version as _pkg_version
from pathlib import Path

from mcp import types
from pydantic import BaseModel, ConfigDict, Field, model_validator

from babi.canon import Json
from babi.collect import build_discover_bundle, discover
from babi.evidence import EvidenceStore
from babi.planner import synthesize_call
from babi.sandbox.credentials import MissingCredentialsError, missing_credentials, redact, require_credentials
from babi.sandbox.synthesize import validates
from babi.target import TargetSpec

_BABI_VERSION = _pkg_version("babi")
DEFAULT_TARGETS_DIR = Path("targets")


class TargetRunOutcome(str, Enum):
    DISCOVERED = "discovered"
    CREDENTIALS_UNAVAILABLE = "credentials_unavailable"
    DISCOVER_FAILED = "discover_failed"


class ToolPlanStatus(str, Enum):
    SUCCESS = "success"
    INVALID = "invalid"
    INCONCLUSIVE = "inconclusive"


class ToolPlanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    tool_subject: str
    status: ToolPlanStatus
    detail: str | None = None


class TargetRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_name: str
    outcome: TargetRunOutcome
    detail: str | None = None
    tool_plans: list[ToolPlanResult] = Field(default_factory=list)
    started_at: str
    finished_at: str


class RunnerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_concurrency: int = Field(default=4, ge=1)
    per_target_timeout_seconds: float = Field(default=45.0, gt=0)
    include_optional_arguments: bool = False


class CorpusRunDenominator(BaseModel):
    """Exact counts a corpus run's headline rates are computed from.
    `extra="forbid"` plus the validator below make an inconsistent
    denominator a construction-time error, not a reporting bug caught
    later: `targets_total` is always the sum of the three target
    outcomes, `tools_total` is always `tools_eligible + tools_inconclusive`,
    and `tools_eligible` is always `tools_success + tools_invalid`."""

    model_config = ConfigDict(extra="forbid")

    targets_total: int = Field(ge=0)
    targets_discovered: int = Field(ge=0)
    targets_credentials_unavailable: int = Field(ge=0)
    targets_discover_failed: int = Field(ge=0)
    tools_total: int = Field(ge=0)
    tools_eligible: int = Field(ge=0)
    tools_success: int = Field(ge=0)
    tools_invalid: int = Field(ge=0)
    tools_inconclusive: int = Field(ge=0)

    @model_validator(mode="after")
    def _arithmetic_is_exact(self) -> CorpusRunDenominator:
        target_sum = self.targets_discovered + self.targets_credentials_unavailable + self.targets_discover_failed
        if target_sum != self.targets_total:
            raise ValueError(
                f"targets_discovered + targets_credentials_unavailable + targets_discover_failed "
                f"({target_sum}) must equal targets_total ({self.targets_total})"
            )
        if self.tools_eligible + self.tools_inconclusive != self.tools_total:
            raise ValueError(
                f"tools_eligible + tools_inconclusive ({self.tools_eligible + self.tools_inconclusive}) "
                f"must equal tools_total ({self.tools_total})"
            )
        if self.tools_success + self.tools_invalid != self.tools_eligible:
            raise ValueError(
                f"tools_success + tools_invalid ({self.tools_success + self.tools_invalid}) "
                f"must equal tools_eligible ({self.tools_eligible})"
            )
        return self


class CorpusRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[TargetRunResult]

    def denominator(self) -> CorpusRunDenominator:
        """Exact eligible/success/invalid/inconclusive counts and the
        exact denominator every rate in a corpus run is computed from."""
        targets_discovered = 0
        targets_credentials_unavailable = 0
        targets_discover_failed = 0
        tools_success = 0
        tools_invalid = 0
        tools_inconclusive = 0

        for target in self.targets:
            if target.outcome is TargetRunOutcome.DISCOVERED:
                targets_discovered += 1
            elif target.outcome is TargetRunOutcome.CREDENTIALS_UNAVAILABLE:
                targets_credentials_unavailable += 1
            else:
                targets_discover_failed += 1

            for plan in target.tool_plans:
                if plan.status is ToolPlanStatus.SUCCESS:
                    tools_success += 1
                elif plan.status is ToolPlanStatus.INVALID:
                    tools_invalid += 1
                else:
                    tools_inconclusive += 1

        tools_eligible = tools_success + tools_invalid
        return CorpusRunDenominator(
            targets_total=len(self.targets),
            targets_discovered=targets_discovered,
            targets_credentials_unavailable=targets_credentials_unavailable,
            targets_discover_failed=targets_discover_failed,
            tools_total=tools_eligible + tools_inconclusive,
            tools_eligible=tools_eligible,
            tools_success=tools_success,
            tools_invalid=tools_invalid,
            tools_inconclusive=tools_inconclusive,
        )


def valid_first_call_rate(denominator: CorpusRunDenominator) -> float | None:
    """`tools_success / tools_eligible` -- RUBRIC.md kill condition 2's
    exact ratio ("valid first call for at least 70% of sandbox-eligible
    tool schemas"). `None` when no tool was eligible at all (nothing to
    divide by), never a fabricated 0.0 or 1.0."""
    if denominator.tools_eligible == 0:
        return None
    return denominator.tools_success / denominator.tools_eligible


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _synthesize_first_call(
    tool: dict[str, Json], *, include_optional: bool
) -> tuple[bool, dict[str, Json], str]:
    input_schema = tool.get("inputSchema")
    if not isinstance(input_schema, dict):
        return False, {}, "planner"
    return True, synthesize_call(tool, include_optional=include_optional), "planner"


def _plan_tool(tool: dict[str, Json], tool_subject_digest: str, *, include_optional: bool) -> ToolPlanResult:
    name = str(tool.get("name", ""))
    ok, arguments, source = _synthesize_first_call(tool, include_optional=include_optional)
    if not ok:
        return ToolPlanResult(
            tool_name=name,
            tool_subject=tool_subject_digest,
            status=ToolPlanStatus.INCONCLUSIVE,
            detail=f"{source} declined: no generic schema-driven argument set available for this inputSchema",
        )

    input_schema = tool.get("inputSchema")
    schema_for_validation = input_schema if isinstance(input_schema, dict) else {}
    if validates(schema_for_validation, arguments):
        return ToolPlanResult(
            tool_name=name, tool_subject=tool_subject_digest, status=ToolPlanStatus.SUCCESS, detail=f"synthesized by {source}"
        )
    return ToolPlanResult(
        tool_name=name,
        tool_subject=tool_subject_digest,
        status=ToolPlanStatus.INVALID,
        detail=f"{source}-synthesized arguments failed validation against the tool's own inputSchema",
    )


async def _run_one_target(
    target: TargetSpec,
    *,
    config: RunnerConfig,
    client_info: types.Implementation,
    semaphore: asyncio.Semaphore,
    evidence_store: EvidenceStore | None,
) -> TargetRunResult:
    started_at = _now_iso()

    missing = missing_credentials(target.env_vars)
    if missing:
        return TargetRunResult(
            target_name=target.name,
            outcome=TargetRunOutcome.CREDENTIALS_UNAVAILABLE,
            detail=f"missing environment variables: {', '.join(missing)}",
            started_at=started_at,
            finished_at=_now_iso(),
        )

    async with semaphore:
        try:
            discovery = await asyncio.wait_for(
                discover(target, client_info=client_info), timeout=config.per_target_timeout_seconds
            )
        except Exception as error:  # noqa: BLE001 -- classified generically below, never assumed to be a planner bug
            # Re-derive from live environment state rather than matching the
            # exception's message: correct even if a credential vanished
            # between the pre-check above and this subprocess spawn.
            still_missing = missing_credentials(target.env_vars)
            if still_missing:
                return TargetRunResult(
                    target_name=target.name,
                    outcome=TargetRunOutcome.CREDENTIALS_UNAVAILABLE,
                    detail=f"missing environment variables: {', '.join(still_missing)}",
                    started_at=started_at,
                    finished_at=_now_iso(),
                )
            try:
                resolved = require_credentials(target.env_vars)
            except MissingCredentialsError:
                resolved = {}
            detail = redact(f"{type(error).__name__}: {error}", resolved.values())
            return TargetRunResult(
                target_name=target.name,
                outcome=TargetRunOutcome.DISCOVER_FAILED,
                detail=detail,
                started_at=started_at,
                finished_at=_now_iso(),
            )

    bundle = build_discover_bundle(discovery, runtime_digests={})
    if evidence_store is not None:
        evidence_store.put_json(bundle)

    tool_plans = [
        _plan_tool(tool, bundle["tool_subjects"][tool["name"]], include_optional=config.include_optional_arguments)
        for tool in bundle["tools_list"]
    ]

    return TargetRunResult(
        target_name=target.name,
        outcome=TargetRunOutcome.DISCOVERED,
        tool_plans=tool_plans,
        started_at=started_at,
        finished_at=_now_iso(),
    )


async def run_targets(
    targets: Sequence[TargetSpec],
    *,
    config: RunnerConfig | None = None,
    client_info: types.Implementation | None = None,
    evidence_store: EvidenceStore | None = None,
) -> CorpusRunSummary:
    """Run DISCOVER plus the deterministic first-call check across
    `targets`, bounded by `config.max_concurrency` concurrent targets and
    `config.per_target_timeout_seconds` per target. Every target produces
    exactly one `TargetRunResult`, in input order."""
    cfg = config or RunnerConfig()
    info = client_info or types.Implementation(name="babi-corpus-runner", version=_BABI_VERSION)
    semaphore = asyncio.Semaphore(cfg.max_concurrency)
    results = await asyncio.gather(
        *(
            _run_one_target(target, config=cfg, client_info=info, semaphore=semaphore, evidence_store=evidence_store)
            for target in targets
        )
    )
    return CorpusRunSummary(targets=list(results))


def _load_targets(targets_dir: Path) -> list[TargetSpec]:
    return [TargetSpec.model_validate(json.loads(path.read_text())) for path in sorted(Path(targets_dir).glob("*.json"))]


async def run_corpus(
    targets_dir: Path | str = DEFAULT_TARGETS_DIR,
    *,
    config: RunnerConfig | None = None,
    client_info: types.Implementation | None = None,
    evidence_store: EvidenceStore | None = None,
) -> CorpusRunSummary:
    """`run_targets` over every `TargetSpec` in `targets_dir` (default:
    `targets/`), i.e. the full corpus run."""
    targets = _load_targets(Path(targets_dir))
    return await run_targets(targets, config=config, client_info=client_info, evidence_store=evidence_store)


__all__ = [
    "CorpusRunDenominator",
    "CorpusRunSummary",
    "RunnerConfig",
    "TargetRunOutcome",
    "TargetRunResult",
    "ToolPlanResult",
    "ToolPlanStatus",
    "run_corpus",
    "run_targets",
    "valid_first_call_rate",
]
