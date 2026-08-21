"""Generic count/digest observer: reduces any already-obtained JSON value
(a tool response, a read-back response, a local snapshot) to a compact,
comparable summary -- a resource count when the value is list-shaped, and a
canonical digest always. Carries no knowledge of what the value represents;
callers decide what to point it at and what the comparison means.
"""

from __future__ import annotations

from dataclasses import dataclass

from babi.canon import Json, digest
from babi.evidence import EvidenceStore
from babi.execute.binding import collection_of
from babi.history import ObservationStatus, ObserverResult

KIND = "count_digest"


@dataclass(frozen=True, slots=True)
class CountDigest:
    """`count` is `None` when `value` was not list-shaped (a single object
    or scalar has nothing to count); `digest` is always populated."""

    count: int | None
    digest: str


def summarize(value: Json) -> CountDigest:
    collection = collection_of(value)
    if collection is not None:
        return CountDigest(count=len(collection), digest=digest(collection))
    return CountDigest(count=None, digest=digest(value if value is not None else {}))


def observe(value: Json, *, evidence: EvidenceStore, detail: str | None = None) -> ObserverResult:
    """Wraps `summarize(value)` as a standalone `ObserverResult`.

    This kind is deliberately *not* the one the classifier treats as
    evidence about a step's own intended effect (`babi.classify` reserves
    that role for `"read_back"`, produced only through the
    depends_on-bound `babi.observe.read_back.read_back`); a bare
    `count_digest` call like this one is always ancillary evidence, e.g. a
    caller-chosen secondary resource such as a log or audit trail.
    """
    try:
        summary = summarize(value)
        evidence_digest = evidence.put_json(value if value is not None else {})
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
        status=ObservationStatus.observed_true,
        value={"count": summary.count, "digest": summary.digest},
        evidence_digest=evidence_digest,
        detail=detail,
    )


__all__ = ["KIND", "CountDigest", "observe", "summarize"]
