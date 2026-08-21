from pathlib import Path

from babi.evidence import EvidenceStore
from babi.history import ObservationStatus
from babi.observe.stderr_exit import observe


def test_observe_observed_true_on_nonzero_exit_with_empty_stderr(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)

    result = observe("", 1, evidence=store)

    assert result.kind == "stderr_exit"
    assert result.status == ObservationStatus.observed_true
    assert result.value == {"exit_code": 1, "stderr_length": 0}
    assert result.evidence_digest is not None
    assert store.get(result.evidence_digest) is not None


def test_observe_observed_true_on_clean_exit_with_nonempty_stderr(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)

    result = observe("warning: deprecated\n", 0, evidence=store)

    assert result.status == ObservationStatus.observed_true
    assert result.value == {"exit_code": 0, "stderr_length": len(b"warning: deprecated\n")}


def test_observe_observed_false_on_clean_exit_and_whitespace_only_stderr(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)

    result = observe("   \n", 0, evidence=store)

    assert result.status == ObservationStatus.observed_false
    assert result.value == {"exit_code": 0, "stderr_length": len("   \n")}


def test_observe_inconclusive_when_exit_code_is_none_uses_default_detail(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)

    result = observe("", None, evidence=store)

    assert result.status == ObservationStatus.inconclusive
    assert result.value is None
    assert result.evidence_digest is None
    assert result.detail == "process has not exited"


def test_observe_inconclusive_when_exit_code_is_none_uses_caller_detail(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)

    result = observe("", None, evidence=store, detail="still running at cleanup")

    assert result.status == ObservationStatus.inconclusive
    assert result.detail == "still running at cleanup"


def test_observe_accepts_bytes_stderr_and_reports_byte_length(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)

    result = observe(b"boom", 1, evidence=store)

    assert result.status == ObservationStatus.observed_true
    assert result.value == {"exit_code": 1, "stderr_length": 4}
    evidence_bytes = store.get(result.evidence_digest)
    assert b'"stderr":"boom"' in evidence_bytes


def test_observe_stderr_length_counts_utf8_bytes_not_characters(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    text = "caf\u00e9"  # 4 characters, 5 UTF-8 bytes

    result = observe(text, 1, evidence=store)

    assert len(text) == 4
    assert result.value == {"exit_code": 1, "stderr_length": 5}


def test_observe_is_inconclusive_when_stderr_has_an_invalid_type(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)

    result = observe(12345, 1, evidence=store, detail="probe step 4")  # type: ignore[arg-type]

    assert result.status == ObservationStatus.inconclusive
    assert result.value is None
    assert result.evidence_digest is None
    assert result.detail is not None and result.detail.startswith("probe step 4: ")
