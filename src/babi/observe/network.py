"""Generic network-egress observer over an already-parsed event log.

This module has zero opinion on how events were captured -- proxy, eBPF,
sidecar, anything else; it only aggregates. Each event is a
`Mapping[str, Json]` with at least a `"destination"` key (str) and
optionally `"bytes"` (int) and `"timestamp"` (str); any other keys are
ignored.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import NotRequired, TypedDict

from babi.evidence import EvidenceStore
from babi.history import ObservationStatus, ObserverResult

KIND = "network"

class NetworkEvent(TypedDict):
    destination: str
    bytes: NotRequired[int]
    timestamp: NotRequired[str]


class NetworkSummary(TypedDict):
    destinations: list[str]
    event_count: int
    total_bytes: int | None


class NetworkDiff(TypedDict):
    new_destinations: list[str]
    event_count_delta: int


def summarize(events: Iterable[NetworkEvent]) -> NetworkSummary:
    """`{"destinations": sorted unique destinations, "event_count": ...,
    "total_bytes": sum of every present "bytes" field, or `None` if none of
    the events carried one}`."""
    destinations: set[str] = set()
    event_count = 0
    total_bytes = 0
    saw_bytes = False
    for event in events:
        destinations.add(event["destination"])
        event_count += 1
        if "bytes" in event:
            total_bytes += event["bytes"]
            saw_bytes = True
    return {
        "destinations": sorted(destinations),
        "event_count": event_count,
        "total_bytes": total_bytes if saw_bytes else None,
    }


def diff_destinations(before: Iterable[NetworkEvent], after: Iterable[NetworkEvent]) -> NetworkDiff:
    """`{"new_destinations": sorted destinations in `after` absent from
    `before`, "event_count_delta": len(after) - len(before)}`."""
    before_events = list(before)
    after_events = list(after)
    before_destinations = {event["destination"] for event in before_events}
    after_destinations = {event["destination"] for event in after_events}
    new_destinations = sorted(after_destinations - before_destinations)
    return {
        "new_destinations": new_destinations,
        "event_count_delta": len(after_events) - len(before_events),
    }


def observe(
    before: Iterable[NetworkEvent],
    after: Iterable[NetworkEvent],
    *,
    evidence: EvidenceStore,
    detail: str | None = None,
) -> ObserverResult:
    """Diffs two network-event logs into a `"network"` observation.
    `before`/`after` are consumed exactly once each (they may be one-shot
    iterables), then reused for both the diff and the evidence summary. A
    malformed event (e.g. missing `"destination"`) degrades this to
    `inconclusive` rather than raising `KeyError`."""
    try:
        before_events = list(before)
        after_events = list(after)
        diff = diff_destinations(before_events, after_events)
        status = ObservationStatus.observed_true if diff["new_destinations"] else ObservationStatus.observed_false
        evidence_digest = evidence.put_json(
            {"before": summarize(before_events), "after": summarize(after_events)}
        )
    except Exception as exc:  # noqa: BLE001 -- a broken observer degrades to inconclusive, never crashes the run
        return ObserverResult(
            kind=KIND,
            status=ObservationStatus.inconclusive,
            value=None,
            evidence_digest=None,
            detail=str(exc) if detail is None else f"{detail}: {exc}",
        )
    return ObserverResult(
        kind=KIND,
        status=status,
        value=diff,
        evidence_digest=evidence_digest,
        detail=detail,
    )


__all__ = ["KIND", "diff_destinations", "observe", "summarize"]
