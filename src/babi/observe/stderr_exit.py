"""Generic stderr/exit-status observer.

Wraps an already-captured stderr stream and exit status; this module never
spawns or manages a process itself, only reports on the two facts a caller
already has about one that ran.
"""

from __future__ import annotations

from babi.evidence import EvidenceStore
from babi.history import ObservationStatus, ObserverResult

KIND = "stderr_exit"

_NOT_EXITED_DETAIL = "process has not exited"


def observe(
    stderr: str | bytes,
    exit_code: int | None,
    *,
    evidence: EvidenceStore,
    detail: str | None = None,
) -> ObserverResult:
    """`exit_code=None` means exit status is not yet available -- that is
    `inconclusive`, never a false claim of a clean exit. Otherwise,
    `observed_true` means there is something concrete to report (a nonzero
    exit, non-empty stderr, or both); `observed_false` means a clean exit
    with no stderr."""
    if exit_code is None:
        return ObserverResult(
            kind=KIND,
            status=ObservationStatus.inconclusive,
            value=None,
            evidence_digest=None,
            detail=_NOT_EXITED_DETAIL if detail is None else detail,
        )
    try:
        if isinstance(stderr, bytes):
            stderr_bytes = stderr
            stderr_text = stderr.decode("utf-8", errors="replace")
        else:
            stderr_text = stderr
            stderr_bytes = stderr.encode("utf-8")
        stderr_length = len(stderr_bytes)
        has_signal = exit_code != 0 or bool(stderr_text.strip())
        status = ObservationStatus.observed_true if has_signal else ObservationStatus.observed_false
        evidence_digest = evidence.put_json({"exit_code": exit_code, "stderr": stderr_text})
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
        value={"exit_code": exit_code, "stderr_length": stderr_length},
        evidence_digest=evidence_digest,
        detail=detail,
    )


__all__ = ["KIND", "observe"]
