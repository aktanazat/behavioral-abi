"""Generic filesystem snapshot/diff observer.

Content-hashes every regular file reachable from a caller-given set of
paths and reports what changed between two such snapshots. This module
carries no opinion on why those paths were chosen or what ran between the
two snapshots -- callers decide what to point it at; this module only ever
sees paths and digests.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from babi.evidence import EvidenceStore
from babi.history import ObservationStatus, ObserverResult

KIND = "filesystem_diff"

_CHUNK_SIZE = 1 << 20


def snapshot(paths: Sequence[str | Path]) -> dict[str, str]:
    """Sha256-digest (hex, no prefix) every regular file reachable from
    `paths`. A path that does not exist contributes nothing -- a
    not-yet-created file is a legitimate pre-snapshot state, not an error.
    Symlinks are never followed, whether passed directly or found while
    walking a directory, so a symlink cycle can never make this loop
    forever; only the files under a directory become keys, never the
    directory itself."""
    files: dict[str, str] = {}
    for raw_path in paths:
        _snapshot_path(Path(raw_path), files)
    return files


def _snapshot_path(path: Path, files: dict[str, str]) -> None:
    if path.is_symlink():
        return
    if path.is_file():
        files[str(path)] = _digest_file(path)
        return
    if not path.is_dir():
        return
    for root, _dirnames, filenames in os.walk(path, followlinks=False):
        for name in filenames:
            file_path = Path(root) / name
            if file_path.is_symlink() or not file_path.is_file():
                continue
            files[str(file_path)] = _digest_file(file_path)


def _digest_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def diff_snapshots(before: Mapping[str, str], after: Mapping[str, str]) -> dict[str, list[str]]:
    """Sorted added/removed/modified path lists between two `snapshot()`
    results."""
    before_paths = before.keys()
    after_paths = after.keys()
    added = sorted(after_paths - before_paths)
    removed = sorted(before_paths - after_paths)
    modified = sorted(path for path in before_paths & after_paths if before[path] != after[path])
    return {"added": added, "removed": removed, "modified": modified}


def observe(
    before: Mapping[str, str],
    after: Mapping[str, str],
    *,
    evidence: EvidenceStore,
    detail: str | None = None,
) -> ObserverResult:
    """Diffs two filesystem snapshots into a `"filesystem_diff"`
    observation. The full added/removed/modified path lists are bulky
    evidence, so only their counts go in `value`; the lists themselves live
    behind `evidence_digest`."""
    try:
        diff = diff_snapshots(before, after)
        has_changes = bool(diff["added"] or diff["removed"] or diff["modified"])
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
        value={"added": len(diff["added"]), "removed": len(diff["removed"]), "modified": len(diff["modified"])},
        evidence_digest=evidence_digest,
        detail=detail,
    )


__all__ = ["KIND", "diff_snapshots", "observe", "snapshot"]
