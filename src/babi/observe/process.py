"""Generic child-process snapshot/diff observer.

Portable across macOS and Linux: the only external dependency is `ps`,
invoked with the POSIX/BSD-and-GNU-compatible `-eo pid,ppid,comm` format
(never `/proc`, which Linux has and macOS does not). Descendants are found
by walking the ppid-chain in-process from a parsed `ps` snapshot, never by
asking the OS for a process tree directly -- another surface that is not
portable.
"""

from __future__ import annotations

import subprocess

from babi.evidence import EvidenceStore
from babi.history import ObservationStatus, ObserverResult

KIND = "process_snapshot"

_PS_TIMEOUT_SECONDS = 10


def snapshot(pid: int) -> list[dict[str, str]]:
    """All descendant processes of `pid` (not including `pid` itself), each
    as `{"pid": ..., "ppid": ..., "comm": ...}`. Safe to call unconditionally:
    returns `[]` if `pid` has no descendants, `ps` is missing, times out, or
    produces anything this can't parse -- callers of `observe()` need the
    try/except at that layer, but `snapshot()` itself never raises."""
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,ppid,comm"],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode != 0:
            return []
        return _descendants(_parse_ps_output(result.stdout), str(pid))
    except Exception:  # noqa: BLE001 -- snapshot() degrades to [] on any ps/parsing trouble
        return []


def _parse_ps_output(raw: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line in raw.splitlines()[1:]:  # first line is the ps header
        fields = line.split(maxsplit=2)
        if len(fields) < 3:
            continue
        entries.append({"pid": fields[0], "ppid": fields[1], "comm": fields[2]})
    return entries


def _descendants(entries: list[dict[str, str]], root_pid: str) -> list[dict[str, str]]:
    children_by_ppid: dict[str, list[dict[str, str]]] = {}
    for entry in entries:
        children_by_ppid.setdefault(entry["ppid"], []).append(entry)

    descendants: list[dict[str, str]] = []
    visited: set[str] = set()
    frontier = [root_pid]
    while frontier:
        parent_pid = frontier.pop()
        for child in children_by_ppid.get(parent_pid, []):
            if child["pid"] in visited:
                continue
            visited.add(child["pid"])
            descendants.append(child)
            frontier.append(child["pid"])
    return descendants


def diff_snapshots(
    before: list[dict[str, str]], after: list[dict[str, str]]
) -> dict[str, list[dict[str, str]]]:
    """`{"added": [...], "removed": [...]}` process dicts, compared by pid."""
    before_by_pid = {entry["pid"]: entry for entry in before}
    after_by_pid = {entry["pid"]: entry for entry in after}
    added = [after_by_pid[pid] for pid in sorted(after_by_pid.keys() - before_by_pid.keys(), key=int)]
    removed = [before_by_pid[pid] for pid in sorted(before_by_pid.keys() - after_by_pid.keys(), key=int)]
    return {"added": added, "removed": removed}


def observe(
    before: list[dict[str, str]],
    after: list[dict[str, str]],
    *,
    evidence: EvidenceStore,
    detail: str | None = None,
) -> ObserverResult:
    """Diffs two process snapshots into a `"process_snapshot"` observation."""
    try:
        diff = diff_snapshots(before, after)
        has_changes = bool(diff["added"] or diff["removed"])
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
        value={"added": len(diff["added"]), "removed": len(diff["removed"])},
        evidence_digest=evidence_digest,
        detail=detail,
    )


__all__ = ["KIND", "diff_snapshots", "observe", "snapshot"]
