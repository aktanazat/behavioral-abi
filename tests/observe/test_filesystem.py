import hashlib
from pathlib import Path

from babi.evidence import EvidenceStore
from babi.history import ObservationStatus
from babi.observe.filesystem import diff_snapshots, observe, snapshot


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_snapshot_digests_files_and_ignores_missing_paths(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"hello")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_bytes(b"world")
    missing = tmp_path / "does-not-exist"

    result = snapshot([tmp_path, missing])

    assert result == {
        str(tmp_path / "a.txt"): _sha256(b"hello"),
        str(sub / "b.txt"): _sha256(b"world"),
    }


def test_snapshot_does_not_follow_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "real.txt").write_bytes(b"real")
    (outside / "secret.txt").write_bytes(b"secret")
    (root / "link_to_file.txt").symlink_to(root / "real.txt")
    (root / "link_to_dir").symlink_to(outside, target_is_directory=True)

    result = snapshot([root])

    assert result == {str(root / "real.txt"): _sha256(b"real")}
    assert str(root / "link_to_file.txt") not in result
    assert str(outside / "secret.txt") not in result


def test_snapshot_of_a_bare_symlink_path_contributes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_bytes(b"data")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    assert snapshot([link]) == {}


def test_diff_snapshots_reports_added_removed_modified() -> None:
    before = {"a": "digest-a", "b": "digest-b", "c": "digest-c"}
    after = {"b": "digest-b-changed", "c": "digest-c", "d": "digest-d"}

    diff = diff_snapshots(before, after)

    assert diff == {"added": ["d"], "removed": ["a"], "modified": ["b"]}


def test_diff_snapshots_empty_when_identical() -> None:
    snap = {"a": "digest-a"}
    assert diff_snapshots(snap, snap) == {"added": [], "removed": [], "modified": []}


def test_observe_reports_observed_true_and_stores_full_diff(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path / "evidence")
    before = {"a": "digest-a", "b": "digest-b"}
    after = {"b": "digest-b-changed", "c": "digest-c"}

    result = observe(before, after, evidence=store)

    assert result.kind == "filesystem_diff"
    assert result.status == ObservationStatus.observed_true
    assert result.value == {"added": 1, "removed": 1, "modified": 1}
    assert result.evidence_digest is not None
    stored = store.get(result.evidence_digest)
    assert stored == store.path(result.evidence_digest).read_bytes()


def test_observe_reports_observed_false_when_snapshots_match(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path / "evidence")
    snap = {"a": "digest-a", "b": "digest-b"}

    result = observe(snap, snap, evidence=store)

    assert result.status == ObservationStatus.observed_false
    assert result.value == {"added": 0, "removed": 0, "modified": 0}
    assert result.evidence_digest is not None


def test_observe_is_inconclusive_when_evidence_store_cannot_write(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"a file, not a directory")
    store = EvidenceStore(root=blocker / "nested")  # every write under this root must fail

    result = observe({"a": "digest-a"}, {"a": "digest-a-changed"}, evidence=store, detail="probe step 3")

    assert result.status == ObservationStatus.inconclusive
    assert result.value is None
    assert result.evidence_digest is None
    assert result.detail is not None and result.detail.startswith("probe step 3: ")
