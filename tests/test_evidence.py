import hashlib
from pathlib import Path

import pytest

from babi import canon
from babi.evidence import EvidenceStore


def test_put_bytes_roundtrip(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    digest = store.put_bytes(b"hello evidence")
    assert digest == f"sha256:{hashlib.sha256(b'hello evidence').hexdigest()}"
    assert store.get(digest) == b"hello evidence"


def test_put_json_uses_canonical_json(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    obj = {"b": 2, "a": 1}
    digest = store.put_json(obj)
    assert digest == canon.digest(obj)
    assert store.get(digest) == canon.canonical_json(obj)


def test_layout_is_sha256_prefixed(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    digest = store.put_bytes(b"content")
    hex_digest = digest.removeprefix("sha256:")
    expected_path = tmp_path / "sha256" / hex_digest[:2] / hex_digest
    assert store.path(digest) == expected_path
    assert expected_path.is_file()


def test_write_once_is_a_noop_for_existing_digest(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    digest = store.put_bytes(b"same content")
    target = store.path(digest)
    original_mtime_ns = target.stat().st_mtime_ns

    # Re-writing identical content must not fail, and should not need to
    # touch the file (write-once semantics).
    digest_again = store.put_bytes(b"same content")
    assert digest_again == digest
    assert target.stat().st_mtime_ns == original_mtime_ns


def test_no_leftover_temp_files_after_write(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.put_bytes(b"content")
    bucket_dirs = list((tmp_path / "sha256").iterdir())
    assert len(bucket_dirs) == 1
    entries = list(bucket_dirs[0].iterdir())
    assert len(entries) == 1
    assert not entries[0].name.startswith(".tmp-")


def test_path_rejects_non_sha256_digest(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    with pytest.raises(ValueError, match="not a sha256 digest"):
        store.path("md5:deadbeef")


def test_get_missing_digest_raises(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    missing = "sha256:" + "0" * 64
    with pytest.raises(FileNotFoundError):
        store.get(missing)


def test_default_root_is_relative_evidence_dir() -> None:
    assert EvidenceStore().root == Path("evidence")
