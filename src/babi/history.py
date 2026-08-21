"""Jepsen-style probe history: the executor's ordered record of everything it
invoked and everything each observer saw about it.

A `ProbeHistory` is the append-only log one `execute_plan` run produces: each
`HistoryEvent` is one tool invocation (whether it completed, errored, or was
transport-interrupted), carrying its own request/response/error and every
`ObserverResult` gathered for it. `sequence` is assigned in the order events
are appended -- for concurrent invocations (`PARALLEL_DOUBLE_FIRE`) that is
completion order, since two overlapping invocations have no single true
serialization point but do have a definite order in which each first became
observable to the executor. This is deliberately coarser than classic
Jepsen's separate `:invoke`/`:ok` log lines (`invoked_at`/`completed_at` on
one record instead of two), a simplification forced by the frozen
`HistoryEvent` field set this module implements verbatim for the rest of the
prober to depend on.

Every model here is `extra="forbid"`: a field neither this module nor its
consumers declared is a bug, not silently-ignored data.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from babi.canon import Json, digest
from babi.plan import ProbePrimitive


class ObservationStatus(str, Enum):
    """What an observer (or, downstream, a classifier) could determine about
    one property or reading. Four states only -- there is no fifth "did not
    check" state distinct from `inconclusive`, and no per-tool fifth state:
    absence of evidence is always `inconclusive`, never inferred into
    `observed_true`/`observed_false`."""

    observed_true = "observed_true"
    observed_false = "observed_false"
    inconclusive = "inconclusive"
    not_applicable = "not_applicable"


class InvocationStatus(str, Enum):
    """How one tool invocation ended, at the transport/protocol level --
    independent of whether the tool's own `CallToolResult.isError` was set,
    which lives in `response` instead. `interrupted` is reserved for the
    specific case this executor can actually confirm: the MCP SDK's own
    courtesy-cancel fired (the request was written, the wait for its
    response was abandoned before one arrived). Any other transport/protocol
    failure is `error`."""

    completed = "completed"
    error = "error"
    interrupted = "interrupted"


class ObserverResult(BaseModel):
    """One observer's finding about one `HistoryEvent`. `kind` is a
    free-text observer-implementation identifier (e.g. `"read_back"`,
    `"filesystem_diff"`) -- never a tool or operation name. `value` is a
    compact, JSON-safe summary; bulky raw evidence backing it lives in the
    `EvidenceStore` under `evidence_digest` instead of inline."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    status: ObservationStatus
    value: Json | None = None
    evidence_digest: str | None = None
    detail: str | None = None


class HistoryEvent(BaseModel):
    """One invocation: the call the executor made, how it ended, and every
    observer's reading of it. `arguments` is the resolved, wire-actual
    argument dict this executor sent -- independent of whatever templated or
    partially-bound form the originating `ProbeStep.parameters` used."""

    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=0)
    step_id: str
    primitive: ProbePrimitive
    tool_subject: str
    tool_name: str
    arguments: dict[str, Json] = Field(default_factory=dict)
    invoked_at: str
    completed_at: str | None = None
    status: InvocationStatus
    response: Json | None = None
    error: str | None = None
    observer_results: list[ObserverResult] = Field(default_factory=list)


class ProbeHistory(BaseModel):
    """The full ordered record of one `execute_plan` run, addressed by
    `run_id` and scoped to the `clean_state_id` it started from -- the unit
    the three-run reproduction aggregator (`babi.classify`) compares across
    independent clean-state runs of the same plan."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(default="history/v0", frozen=True)
    run_id: str
    plan_digest: str
    target_subject: str
    clean_state_id: str
    environment_digest: str
    events: list[HistoryEvent] = Field(default_factory=list)
    started_at: str
    finished_at: str | None = None


def history_digest(history: ProbeHistory) -> str:
    """Canonical evidence digest for a full `ProbeHistory`, matching how any
    other evidence blob in this project is addressed (`babi.canon.digest`).
    Callers that must persist the history use this as the key they write it
    under via `EvidenceStore.put_json`; kept here (rather than recomputed ad
    hoc per caller) so every consumer addresses the exact same bytes."""
    return digest(history.model_dump(mode="json"))


__all__ = [
    "HistoryEvent",
    "InvocationStatus",
    "ObservationStatus",
    "ObserverResult",
    "ProbeHistory",
    "history_digest",
]
