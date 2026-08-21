"""Deterministic executor: runs a `ProbePlan` against one connected MCP
session and produces a Jepsen-style `ProbeHistory`.

Six primitives are implemented, chosen to be the ones a caller can compose
into the default pipeline (IMPLEMENTATION-PLAN.md: `CALL_ONCE -> READ_BACK ->
REPEAT | RETRY_AFTER_INTERRUPT | PARALLEL_DOUBLE_FIRE -> VERIFY_WITH_TIMING`)
plus dependency-driven read-back:

- `CALL_ONCE`, `REPEAT`: one or more plain sequential invocations.
- `RETRY_AFTER_INTERRUPT`: one transport-interrupted attempt
  (`babi.execute.interrupt`), then one plain retry.
- `PARALLEL_DOUBLE_FIRE`: N concurrent invocations, each also attempting
  transport interruption (the realistic "double fire" scenario: a client
  cancels one in-flight call and fires another before seeing the first
  resolve).
- `READ_BACK`: one generic black-box read-back (`babi.observe.read_back`)
  per `HistoryEvent` already recorded for the step's primary dependency --
  so `stepA (REPEAT, count=3) -> stepB (READ_BACK, depends_on=[stepA])`
  yields three read-backs, one after each repeat, with no need for the
  planner to emit three separate READ_BACK steps.
- `VERIFY_WITH_TIMING`: the same per-dependency-event fan-out, but each is a
  read-back polling loop with increasing delay, recording elapsed time to
  visibility (or exhausting the schedule).

Any other `ProbePrimitive` (`DISCOVER`, `INSTANTIATE`, `PERTURB_AND_UNDO`,
`BOUNDARY_MUTATION_FUZZ`, `CROSS_CALL_CONSISTENCY`, `PRECONDITION_REVOCATION`)
is recorded as an `error`-status event explaining it is not implemented,
rather than silently skipped or guessed at -- a plan referencing one still
gets a complete, honest history.

No step here is ever dispatched on a tool or operation *name*; every branch
is keyed on `ProbePrimitive` (a fixed, generic vocabulary) or on schema
shape (`babi.execute.binding`).
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from mcp import types
from mcp.client import Client, Transport
from mcp.client.stdio import StdioServerParameters, stdio_client

from babi.canon import Json, digest
from babi.evidence import EvidenceStore
from babi.execute.binding import build_source_pool
from babi.execute.interrupt import DEFAULT_INTERRUPT_BUDGET_SECONDS, attempt_interruptible_call
from babi.execute.wire import dump_model
from babi.history import HistoryEvent, InvocationStatus, ObservationStatus, ObserverResult, ProbeHistory
from babi.observe.read_back import read_back
from babi.plan import ProbePlan, ProbePrimitive, ProbeStep
from babi.target import TargetSpec

_BASE_ENVIRONMENT_NAMES = ("HOME", "LANG", "LC_ALL", "PATH", "TMPDIR")
_DEFAULT_REPEAT_COUNT = 2
# Integer *_ms, never float seconds: `babi.canon.canonical_json` (RFC 8785 JCS,
# restricted to integral numbers per its own docstring) rejects any
# non-integral float, and every one of these values ends up inside a
# `ProbePlan`/`ProbeHistory` that gets canonicalized and digested. Sub-second
# precision -- essential for an interrupt budget -- is only representable
# this way.
_DEFAULT_INTERRUPT_BUDGET_MS = round(DEFAULT_INTERRUPT_BUDGET_SECONDS * 1000)
_DEFAULT_TIMING_DELAYS_MS = (0, 50, 100, 250, 500, 1000)


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    history: ProbeHistory
    history_digest: str


@dataclass(slots=True)
class _RawInvocation:
    tool_name: str
    arguments: dict[str, Json]
    invoked_at: str
    completed_at: str | None
    status: InvocationStatus
    response: Json | None
    error: str | None
    observer_results: list[ObserverResult] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stdio_environment(target: TargetSpec) -> dict[str, str]:
    """Mirrors `babi.collect.discover`'s allowlist convention: only the base
    interpreter/runtime variables plus whatever the target explicitly
    declares via `env_vars` reach the subprocess -- never the full parent
    environment."""
    missing = [name for name in target.env_vars if name not in os.environ]
    if missing:
        raise ValueError(f"target environment variables are unset: {', '.join(missing)}")
    names = (*_BASE_ENVIRONMENT_NAMES, *target.env_vars)
    return {name: os.environ[name] for name in names if name in os.environ}


def _resolve_server(target: TargetSpec) -> Transport | str:
    if target.transport == "stdio":
        argv = [*(target.command or []), *target.args]
        params = StdioServerParameters(command=argv[0], args=argv[1:], env=_stdio_environment(target))
        return stdio_client(params)
    assert target.endpoint is not None  # TargetSpec's validator guarantees this whenever transport isn't stdio
    return target.endpoint


def build_client(target: TargetSpec, *, client_info: types.Implementation) -> Client:
    """An unentered `Client` for `target`; enter it with `async with` (or via
    `execute_plan_against_target`, which does so for you)."""
    return Client(server=_resolve_server(target), mode="auto", client_info=client_info)


def _tool_maps(discovery_bundle: Mapping[str, Json]) -> tuple[dict[str, str], dict[str, dict[str, Json]]]:
    tool_subjects = discovery_bundle.get("tool_subjects")
    if not isinstance(tool_subjects, dict):
        raise ValueError("discovery_bundle is missing a `tool_subjects` mapping")
    subject_to_name = {subject: name for name, subject in tool_subjects.items()}

    catalog = discovery_bundle.get("tools_list")
    tools_by_name = {
        tool["name"]: tool for tool in (catalog if isinstance(catalog, list) else []) if isinstance(tool, dict)
    }
    return subject_to_name, tools_by_name


def _environment_digest(discovery_bundle: Mapping[str, Json]) -> str:
    return digest(
        {
            "transport": discovery_bundle.get("transport"),
            "negotiated_protocol_revision": discovery_bundle.get("negotiated_protocol_revision"),
            "runtime_digests": discovery_bundle.get("runtime_digests"),
        }
    )


def _resolve_tool_name(tool_subject: str, subject_to_name: Mapping[str, str]) -> str:
    name = subject_to_name.get(tool_subject)
    if name is None:
        raise ValueError(f"tool_subject {tool_subject!r} is not present in the discovery bundle")
    return name


def _step_arguments(step: ProbeStep) -> dict[str, Json]:
    arguments = step.parameters.get("arguments", {})
    return dict(arguments) if isinstance(arguments, dict) else {}


def _int_param(step: ProbeStep, key: str, *, default: int) -> int:
    value = step.parameters.get(key, default)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _ms_to_seconds(milliseconds: int) -> float:
    return milliseconds / 1000.0


def _delays_ms_param(step: ProbeStep) -> list[int]:
    value = step.parameters.get("delays_ms")
    if isinstance(value, list) and value and all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        return list(value)
    return list(_DEFAULT_TIMING_DELAYS_MS)


def _split_dependencies(depends_on: Sequence[str]) -> tuple[str | None, list[str]]:
    if not depends_on:
        return None, []
    return depends_on[0], list(depends_on[1:])


def _topological_order(steps: Sequence[ProbeStep]) -> list[ProbeStep]:
    by_id = {step.id: step for step in steps}
    if len(by_id) != len(steps):
        raise ValueError("ProbePlan.steps has duplicate step ids")
    for step in steps:
        for dep in step.depends_on:
            if dep not in by_id:
                raise ValueError(f"step {step.id!r} depends on unknown step {dep!r}")

    ordered: list[ProbeStep] = []
    done: set[str] = set()
    remaining = list(steps)
    while remaining:
        progressed = False
        for step in list(remaining):
            if all(dep in done for dep in step.depends_on):
                ordered.append(step)
                done.add(step.id)
                remaining.remove(step)
                progressed = True
        if not progressed:
            cycle = ", ".join(step.id for step in remaining)
            raise ValueError(f"ProbePlan.steps has a dependency cycle among: {cycle}")
    return ordered


async def _perform_call(client: Client, tool_name: str, arguments: dict[str, Json]) -> _RawInvocation:
    invoked_at = _now_iso()
    try:
        result = await client.call_tool(tool_name, arguments)
    except Exception as exc:  # noqa: BLE001 -- any transport/protocol failure is a valid, honestly-recorded outcome
        return _RawInvocation(
            tool_name=tool_name,
            arguments=arguments,
            invoked_at=invoked_at,
            completed_at=_now_iso(),
            status=InvocationStatus.error,
            response=None,
            error=str(exc),
        )
    return _RawInvocation(
        tool_name=tool_name,
        arguments=arguments,
        invoked_at=invoked_at,
        completed_at=_now_iso(),
        status=InvocationStatus.completed,
        response=dump_model(result),
        error=None,
    )


async def _perform_interruptible_call(
    client: Client, tool_name: str, arguments: dict[str, Json], *, budget_ms: int
) -> _RawInvocation:
    invoked_at = _now_iso()
    attempt = await attempt_interruptible_call(client, tool_name, arguments, budget_seconds=_ms_to_seconds(budget_ms))
    completed_at = _now_iso()

    if attempt.interrupted:
        return _RawInvocation(
            tool_name=tool_name,
            arguments=arguments,
            invoked_at=invoked_at,
            completed_at=completed_at,
            status=InvocationStatus.interrupted,
            response=None,
            error=f"transport interrupted after send, before response (budget={budget_ms}ms)",
            observer_results=[
                ObserverResult(
                    kind="transport_interrupt",
                    status=ObservationStatus.observed_true,
                    value={"budget_ms": budget_ms},
                    detail="the MCP SDK's own courtesy-cancel fired before a response arrived",
                )
            ],
        )
    if attempt.error is not None:
        return _RawInvocation(
            tool_name=tool_name,
            arguments=arguments,
            invoked_at=invoked_at,
            completed_at=completed_at,
            status=InvocationStatus.error,
            response=None,
            error=str(attempt.error),
        )
    assert attempt.result is not None
    return _RawInvocation(
        tool_name=tool_name,
        arguments=arguments,
        invoked_at=invoked_at,
        completed_at=completed_at,
        status=InvocationStatus.completed,
        response=dump_model(attempt.result),
        error=None,
        observer_results=[
            ObserverResult(
                kind="transport_interrupt",
                status=ObservationStatus.inconclusive,
                value={"budget_ms": budget_ms},
                detail="the response arrived before the interrupt budget elapsed; interruption was not observed",
            )
        ],
    )


_HandlerContext = dict[str, Any]


async def _append_with_readbacks(step: ProbeStep, raw: _RawInvocation, ctx: _HandlerContext) -> HistoryEvent:
    event = ctx["append"](step, raw)
    for read_back_step in ctx["readbacks_by_dependency"].get(step.id, []):
        await _read_back_for_event(read_back_step, event, ctx)
    return event


async def _handle_call_once(step: ProbeStep, ctx: _HandlerContext) -> None:
    tool_name = _resolve_tool_name(step.tool_subject, ctx["subject_to_name"])
    raw = await _perform_call(ctx["client"], tool_name, _step_arguments(step))
    await _append_with_readbacks(step, raw, ctx)


async def _handle_repeat(step: ProbeStep, ctx: _HandlerContext) -> None:
    tool_name = _resolve_tool_name(step.tool_subject, ctx["subject_to_name"])
    arguments = _step_arguments(step)
    for _ in range(_int_param(step, "count", default=_DEFAULT_REPEAT_COUNT)):
        raw = await _perform_call(ctx["client"], tool_name, arguments)
        await _append_with_readbacks(step, raw, ctx)


async def _handle_retry_after_interrupt(step: ProbeStep, ctx: _HandlerContext) -> None:
    client = ctx["client"]
    tool_name = _resolve_tool_name(step.tool_subject, ctx["subject_to_name"])
    arguments = _step_arguments(step)
    budget_ms = _int_param(step, "interrupt_budget_ms", default=_DEFAULT_INTERRUPT_BUDGET_MS)

    interrupted_attempt = await _perform_interruptible_call(client, tool_name, arguments, budget_ms=budget_ms)
    await _append_with_readbacks(step, interrupted_attempt, ctx)

    retry = await _perform_call(client, tool_name, arguments)
    await _append_with_readbacks(step, retry, ctx)


async def _handle_parallel_double_fire(step: ProbeStep, ctx: _HandlerContext) -> None:
    client = ctx["client"]
    tool_name = _resolve_tool_name(step.tool_subject, ctx["subject_to_name"])
    arguments = _step_arguments(step)
    budget_ms = _int_param(step, "interrupt_budget_ms", default=_DEFAULT_INTERRUPT_BUDGET_MS)
    count = _int_param(step, "count", default=_DEFAULT_REPEAT_COUNT)

    async def _one() -> None:
        raw = await _perform_interruptible_call(client, tool_name, arguments, budget_ms=budget_ms)
        await _append_with_readbacks(step, raw, ctx)

    await asyncio.gather(*(_one() for _ in range(count)))


async def _read_back_for_event(step: ProbeStep, dep_event: HistoryEvent, ctx: _HandlerContext) -> None:
    client = ctx["client"]
    tool_name = _resolve_tool_name(step.tool_subject, ctx["subject_to_name"])
    input_schema = ctx["tools_by_name"].get(tool_name, {}).get("inputSchema", {}) or {}
    literal_arguments = _step_arguments(step) or None
    extra_deps = list(step.depends_on[1:])
    events_by_step = ctx["events_by_step"]
    extra_responses = [events_by_step[dep][-1].response for dep in extra_deps if events_by_step.get(dep)]
    source_pool = build_source_pool([dep_event.response, *extra_responses])
    invoked_at = _now_iso()
    outcome = await read_back(
        client,
        tool_name=tool_name,
        input_schema=input_schema,
        source_pool=source_pool,
        literal_arguments=literal_arguments,
        evidence=ctx["evidence"],
    )
    arguments = outcome.result.value.get("arguments", {}) if isinstance(outcome.result.value, dict) else {}
    raw = _RawInvocation(
        tool_name=tool_name,
        arguments=arguments if isinstance(arguments, dict) else {},
        invoked_at=invoked_at,
        completed_at=_now_iso(),
        status=InvocationStatus.completed if outcome.response is not None else InvocationStatus.error,
        response=outcome.response,
        error=None if outcome.response is not None else outcome.result.detail,
        observer_results=[outcome.result],
    )
    ctx["append"](step, raw)
    dep_event.observer_results.append(outcome.result)


async def _handle_read_back(step: ProbeStep, ctx: _HandlerContext) -> None:
    primary_dep, _ = _split_dependencies(step.depends_on)
    if primary_dep is None:
        raise ValueError(f"READ_BACK step {step.id!r} has no depends_on to verify")
    for dep_event in ctx["events_by_step"].get(primary_dep, []):
        if not any(result.kind == "read_back" for result in dep_event.observer_results):
            await _read_back_for_event(step, dep_event, ctx)




async def _handle_verify_with_timing(step: ProbeStep, ctx: _HandlerContext) -> None:
    client = ctx["client"]
    tool_name = _resolve_tool_name(step.tool_subject, ctx["subject_to_name"])
    input_schema = ctx["tools_by_name"].get(tool_name, {}).get("inputSchema", {}) or {}
    literal_arguments = _step_arguments(step) or None
    evidence = ctx["evidence"]
    delays_ms = _delays_ms_param(step)

    primary_dep, extra_deps = _split_dependencies(step.depends_on)
    if primary_dep is None:
        raise ValueError(f"VERIFY_WITH_TIMING step {step.id!r} has no depends_on to verify")
    events_by_step = ctx["events_by_step"]
    extra_responses = [events_by_step[dep][-1].response for dep in extra_deps if events_by_step.get(dep)]

    for dep_event in events_by_step.get(primary_dep, []):
        source_pool = build_source_pool([dep_event.response, *extra_responses])
        start = time.monotonic()
        visible = False
        elapsed_ms_at_visible: int | None = None
        last_event: HistoryEvent | None = None
        final_read_back: ObserverResult | None = None

        for checkpoint_ms in delays_ms:
            sleep_for = _ms_to_seconds(checkpoint_ms) - (time.monotonic() - start)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

            invoked_at = _now_iso()
            outcome = await read_back(
                client,
                tool_name=tool_name,
                input_schema=input_schema,
                source_pool=source_pool,
                literal_arguments=literal_arguments,
                evidence=evidence,
            )
            completed_at = _now_iso()
            elapsed_ms = round((time.monotonic() - start) * 1000)
            found = outcome.result.status == ObservationStatus.observed_true
            timing_result = ObserverResult(
                kind="timing",
                status=ObservationStatus.observed_true if found else ObservationStatus.observed_false,
                value={"elapsed_ms": elapsed_ms, "visible": found},
                evidence_digest=outcome.result.evidence_digest,
            )
            arguments = outcome.result.value.get("arguments", {}) if isinstance(outcome.result.value, dict) else {}
            raw = _RawInvocation(
                tool_name=tool_name,
                arguments=arguments if isinstance(arguments, dict) else {},
                invoked_at=invoked_at,
                completed_at=completed_at,
                status=InvocationStatus.completed if outcome.response is not None else InvocationStatus.error,
                response=outcome.response,
                error=None if outcome.response is not None else outcome.result.detail,
                observer_results=[outcome.result, timing_result],
            )
            last_event = ctx["append"](step, raw)
            final_read_back = outcome.result
            if found:
                visible = True
                elapsed_ms_at_visible = elapsed_ms
                break

        if last_event is not None:
            last_event.observer_results.append(
                ObserverResult(
                    kind="timing_summary",
                    status=ObservationStatus.observed_true if visible else ObservationStatus.observed_false,
                    value={"visible": visible, "elapsed_ms": elapsed_ms_at_visible},
                    detail=None if visible else f"not visible within a {delays_ms[-1]}ms polling budget",
                )
            )
        if final_read_back is not None:
            # Same flattening as READ_BACK: only the settled (last-attempted)
            # reading -- not every intermediate poll -- becomes evidence about
            # the verified call itself, so a call whose effect simply took a
            # few polls to become visible is never mistaken for one whose
            # effect changed between polls.
            dep_event.observer_results.append(final_read_back)


async def _handle_unsupported(step: ProbeStep, ctx: _HandlerContext) -> None:
    tool_name = ctx["subject_to_name"].get(step.tool_subject, step.tool_subject)
    now = _now_iso()
    raw = _RawInvocation(
        tool_name=tool_name,
        arguments=_step_arguments(step),
        invoked_at=now,
        completed_at=now,
        status=InvocationStatus.error,
        response=None,
        error=f"primitive {step.primitive.value!r} is not implemented by this executor",
    )
    ctx["append"](step, raw)


_HANDLERS: dict[ProbePrimitive, Callable[[ProbeStep, _HandlerContext], Awaitable[None]]] = {
    ProbePrimitive.CALL_ONCE: _handle_call_once,
    ProbePrimitive.REPEAT: _handle_repeat,
    ProbePrimitive.RETRY_AFTER_INTERRUPT: _handle_retry_after_interrupt,
    ProbePrimitive.PARALLEL_DOUBLE_FIRE: _handle_parallel_double_fire,
    ProbePrimitive.READ_BACK: _handle_read_back,
    ProbePrimitive.VERIFY_WITH_TIMING: _handle_verify_with_timing,
}


async def execute_plan(
    plan: ProbePlan,
    *,
    client: Client,
    discovery_bundle: Mapping[str, Json],
    evidence: EvidenceStore,
    run_id: str | None = None,
    clean_state_id: str | None = None,
) -> ExecutionResult:
    """Run every step of `plan`, in dependency order, against `client` (an
    already-entered `Client` session -- production callers get one from
    `execute_plan_against_target`; tests may inject one connected to an
    in-process fake server directly, bypassing real transport entirely).

    Persists the resulting `ProbeHistory` through `evidence` before
    returning, so a caller never has to remember to do so separately.
    """
    subject_to_name, tools_by_name = _tool_maps(discovery_bundle)

    events: list[HistoryEvent] = []
    events_by_step: dict[str, list[HistoryEvent]] = {}

    def _append(step: ProbeStep, raw: _RawInvocation) -> HistoryEvent:
        event = HistoryEvent(
            sequence=len(events),
            step_id=step.id,
            primitive=step.primitive,
            tool_subject=step.tool_subject,
            tool_name=raw.tool_name,
            arguments=raw.arguments,
            invoked_at=raw.invoked_at,
            completed_at=raw.completed_at,
            status=raw.status,
            response=raw.response,
            error=raw.error,
            observer_results=raw.observer_results,
        )
        events.append(event)
        events_by_step.setdefault(step.id, []).append(event)
        return event

    readbacks_by_dependency: dict[str, list[ProbeStep]] = {}
    for candidate in plan.steps:
        if candidate.primitive is ProbePrimitive.READ_BACK and candidate.depends_on:
            readbacks_by_dependency.setdefault(candidate.depends_on[0], []).append(candidate)

    ctx: _HandlerContext = {
        "client": client,
        "subject_to_name": subject_to_name,
        "tools_by_name": tools_by_name,
        "events_by_step": events_by_step,
        "evidence": evidence,
        "append": _append,
        "readbacks_by_dependency": readbacks_by_dependency,
    }

    started_at = _now_iso()
    for step in _topological_order(plan.steps):
        handler = _HANDLERS.get(step.primitive, _handle_unsupported)
        await handler(step, ctx)
    finished_at = _now_iso()

    history = ProbeHistory(
        run_id=run_id or uuid4().hex,
        plan_digest=digest(plan.model_dump(mode="json")),
        target_subject=plan.subject_digest,
        clean_state_id=clean_state_id or uuid4().hex,
        environment_digest=_environment_digest(discovery_bundle),
        events=events,
        started_at=started_at,
        finished_at=finished_at,
    )
    history_digest_value = evidence.put_json(history.model_dump(mode="json"))
    return ExecutionResult(history=history, history_digest=history_digest_value)


async def execute_plan_against_target(
    plan: ProbePlan,
    target: TargetSpec,
    *,
    discovery_bundle: Mapping[str, Json],
    evidence: EvidenceStore,
    client_info: types.Implementation,
    run_id: str | None = None,
    clean_state_id: str | None = None,
) -> ExecutionResult:
    """Convenience wrapper: connect to `target` (mirroring
    `babi.collect.discover`'s stdio/streamable-http resolution), then run
    `execute_plan` over that one live session."""
    async with AsyncExitStack() as stack:
        client = build_client(target, client_info=client_info)
        await stack.enter_async_context(client)
        return await execute_plan(
            plan,
            client=client,
            discovery_bundle=discovery_bundle,
            evidence=evidence,
            run_id=run_id,
            clean_state_id=clean_state_id,
        )


__all__ = [
    "ExecutionResult",
    "build_client",
    "execute_plan",
    "execute_plan_against_target",
]
