"""Generic container-filesystem-diff observer: a thin wrapper over
`docker diff`. Carries no opinion on what ran inside the container between
snapshots, only the container id to diff.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

from babi.evidence import EvidenceStore
from babi.history import ObservationStatus, ObserverResult

KIND = "container_diff"

_DOCKER_DIFF_TIMEOUT_SECONDS = 30


def run_diff(
    container_id: str,
    *,
    invoke: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """Raw `docker diff <container_id>` stdout. Raises `RuntimeError` on a
    nonzero exit and propagates a missing `docker` executable."""
    result = invoke(
        ["docker", "diff", container_id],
        capture_output=True,
        text=True,
        timeout=_DOCKER_DIFF_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or f"docker diff exited with status {result.returncode}")
    return result.stdout


def parse_diff(raw: str) -> dict[str, list[str]]:
    """Parses `docker diff` lines (`"A <path>"`, `"C <path>"`, `"D <path>"`)
    into sorted `{"added": [...], "changed": [...], "deleted": [...]}` path
    lists. Unrecognized lines are ignored."""
    added: list[str] = []
    changed: list[str] = []
    deleted: list[str] = []
    for line in raw.splitlines():
        marker, sep, path = line.partition(" ")
        if not sep:
            continue
        if marker == "A":
            added.append(path)
        elif marker == "C":
            changed.append(path)
        elif marker == "D":
            deleted.append(path)
    return {"added": sorted(added), "changed": sorted(changed), "deleted": sorted(deleted)}


def observe(
    container_id: str,
    *,
    evidence: EvidenceStore,
    run: Callable[[str], str] = run_diff,
    detail: str | None = None,
) -> ObserverResult:
    """Diffs one container's filesystem into a `"container_diff"`
    observation. `run` defaults to the real `run_diff` but is
    dependency-injected so tests never need a real `docker` daemon."""
    try:
        diff = parse_diff(run(container_id))
        has_changes = bool(diff["added"] or diff["changed"] or diff["deleted"])
        status = ObservationStatus.observed_true if has_changes else ObservationStatus.observed_false
        evidence_digest = evidence.put_json(diff)
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
        value={"added": len(diff["added"]), "changed": len(diff["changed"]), "deleted": len(diff["deleted"])},
        evidence_digest=evidence_digest,
        detail=detail,
    )


__all__ = ["KIND", "observe", "parse_diff", "run_diff"]
