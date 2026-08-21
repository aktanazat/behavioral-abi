"""Deterministic executor: runs a `babi.plan.ProbePlan` against a connected
MCP session and produces a `babi.history.ProbeHistory`.
"""

from __future__ import annotations

from babi.execute.binding import (
    bind_arguments,
    build_source_pool,
    candidate_fields,
    collection_of,
    extract_structured_value,
)
from babi.execute.executor import ExecutionResult, build_client, execute_plan, execute_plan_against_target
from babi.execute.interrupt import DEFAULT_INTERRUPT_BUDGET_SECONDS, InterruptAttempt, attempt_interruptible_call
from babi.execute.wire import dump_model

__all__ = [
    "DEFAULT_INTERRUPT_BUDGET_SECONDS",
    "ExecutionResult",
    "InterruptAttempt",
    "attempt_interruptible_call",
    "bind_arguments",
    "build_client",
    "build_source_pool",
    "candidate_fields",
    "collection_of",
    "dump_model",
    "execute_plan",
    "execute_plan_against_target",
    "extract_structured_value",
]
