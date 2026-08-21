import subprocess
from pathlib import Path

import pytest

from babi.evidence import EvidenceStore
from babi.history import ObservationStatus
from babi.observe.container import observe, parse_diff, run_diff


def test_parse_diff_categorizes_added_changed_deleted_and_ignores_unknown_lines() -> None:
    raw = "C /etc\nA /etc/newfile\nD /etc/oldfile\nA /var/log/some app.log\nX /ignored/line\n"

    diff = parse_diff(raw)

    assert diff == {
        "added": ["/etc/newfile", "/var/log/some app.log"],
        "changed": ["/etc"],
        "deleted": ["/etc/oldfile"],
    }


def test_parse_diff_empty_string_yields_empty_lists() -> None:
    assert parse_diff("") == {"added": [], "changed": [], "deleted": []}


def test_parse_diff_sorts_each_category() -> None:
    raw = "A /z\nA /a\nA /m\n"
    assert parse_diff(raw)["added"] == ["/a", "/m", "/z"]


def test_run_diff_returns_raw_stdout_on_success() -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="C /etc\n", stderr="")

    assert run_diff("some-container", invoke=fake_run) == "C /etc\n"


def test_run_diff_raises_runtime_error_on_nonzero_exit() -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="no such container")

    with pytest.raises(RuntimeError, match="no such container"):
        run_diff("missing-container", invoke=fake_run)


def test_run_diff_propagates_missing_docker_binary() -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("docker")

    with pytest.raises(FileNotFoundError):
        run_diff("some-container", invoke=fake_run)


def test_observe_reports_observed_true_with_injected_run(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)

    def fake_run(container_id: str) -> str:
        assert container_id == "abc123"
        return "A /new\nC /etc/passwd\nD /old\n"

    result = observe("abc123", evidence=store, run=fake_run)

    assert result.kind == "container_diff"
    assert result.status == ObservationStatus.observed_true
    assert result.value == {"added": 1, "changed": 1, "deleted": 1}
    assert result.evidence_digest is not None


def test_observe_reports_observed_false_when_diff_is_empty(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)

    result = observe("abc123", evidence=store, run=lambda container_id: "")

    assert result.status == ObservationStatus.observed_false
    assert result.value == {"added": 0, "changed": 0, "deleted": 0}


def test_observe_is_inconclusive_when_run_raises(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)

    def raising_run(container_id: str) -> str:
        raise RuntimeError("docker daemon not reachable")

    result = observe("abc123", evidence=store, run=raising_run, detail="probe step 2")

    assert result.status == ObservationStatus.inconclusive
    assert result.value is None
    assert result.evidence_digest is None
    assert result.detail == "probe step 2: docker daemon not reachable"


def test_observe_is_inconclusive_when_docker_binary_is_missing(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)

    def missing_binary_run(container_id: str) -> str:
        raise FileNotFoundError("docker")

    result = observe("abc123", evidence=store, run=missing_binary_run)

    assert result.status == ObservationStatus.inconclusive
    assert result.value is None
    assert result.evidence_digest is None
