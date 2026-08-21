import os
import subprocess
import sys
import time
from pathlib import Path

from babi.evidence import EvidenceStore
from babi.history import ObservationStatus
from babi.observe.process import diff_snapshots, observe, snapshot

_IMPLAUSIBLE_PID = 999_999_999


def test_snapshot_finds_a_real_spawned_child() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
    try:
        found: dict[str, str] | None = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            descendants = snapshot(os.getpid())
            matches = [entry for entry in descendants if entry["pid"] == str(child.pid)]
            if matches:
                found = matches[0]
                break
            time.sleep(0.1)

        assert found is not None, "spawned child pid never appeared in snapshot(os.getpid())"
        assert found["ppid"] == str(os.getpid())
        assert found.keys() == {"pid", "ppid", "comm"}
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_snapshot_returns_empty_list_for_pid_with_no_descendants() -> None:
    assert snapshot(_IMPLAUSIBLE_PID) == []


def test_diff_snapshots_reports_added_and_removed_by_pid() -> None:
    before = [
        {"pid": "100", "ppid": "1", "comm": "keep"},
        {"pid": "101", "ppid": "1", "comm": "goes-away"},
    ]
    after = [
        {"pid": "100", "ppid": "1", "comm": "keep"},
        {"pid": "102", "ppid": "1", "comm": "new-arrival"},
    ]

    diff = diff_snapshots(before, after)

    assert diff == {
        "added": [{"pid": "102", "ppid": "1", "comm": "new-arrival"}],
        "removed": [{"pid": "101", "ppid": "1", "comm": "goes-away"}],
    }


def test_diff_snapshots_empty_when_identical() -> None:
    snap = [{"pid": "100", "ppid": "1", "comm": "keep"}]
    assert diff_snapshots(snap, snap) == {"added": [], "removed": []}


def test_observe_reports_observed_true_and_stores_full_diff(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    before = [{"pid": "100", "ppid": "1", "comm": "keep"}]
    after = [
        {"pid": "100", "ppid": "1", "comm": "keep"},
        {"pid": "200", "ppid": "100", "comm": "child"},
    ]

    result = observe(before, after, evidence=store)

    assert result.kind == "process_snapshot"
    assert result.status == ObservationStatus.observed_true
    assert result.value == {"added": 1, "removed": 0}
    assert result.evidence_digest is not None
    assert store.get(result.evidence_digest) is not None


def test_observe_reports_observed_false_when_no_change(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    snap = [{"pid": "100", "ppid": "1", "comm": "keep"}]

    result = observe(snap, snap, evidence=store)

    assert result.status == ObservationStatus.observed_false
    assert result.value == {"added": 0, "removed": 0}


def test_observe_is_inconclusive_on_malformed_process_entry(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    before: list[dict[str, str]] = []
    after = [{"ppid": "1", "comm": "no-pid-key"}]  # missing required "pid" key

    result = observe(before, after, evidence=store, detail="probe step 7")

    assert result.status == ObservationStatus.inconclusive
    assert result.value is None
    assert result.evidence_digest is None
    assert result.detail is not None and result.detail.startswith("probe step 7: ")
