from pathlib import Path

from babi.evidence import EvidenceStore
from babi.history import ObservationStatus
from babi.observe.network import diff_destinations, observe, summarize


def test_summarize_aggregates_destinations_and_bytes() -> None:
    events = [
        {"destination": "b.example.com", "bytes": 200, "timestamp": "t2"},
        {"destination": "a.example.com", "bytes": 100, "timestamp": "t1"},
        {"destination": "a.example.com", "bytes": 50},
    ]

    summary = summarize(events)

    assert summary == {
        "destinations": ["a.example.com", "b.example.com"],
        "event_count": 3,
        "total_bytes": 350,
    }


def test_summarize_total_bytes_is_none_when_no_event_has_bytes() -> None:
    events = [{"destination": "a.example.com"}, {"destination": "b.example.com", "timestamp": "t1"}]

    summary = summarize(events)

    assert summary["total_bytes"] is None
    assert summary["event_count"] == 2


def test_summarize_sums_only_events_that_carry_bytes() -> None:
    events = [{"destination": "a.example.com", "bytes": 10}, {"destination": "b.example.com"}]

    assert summarize(events)["total_bytes"] == 10


def test_diff_destinations_reports_new_destinations_and_event_count_delta() -> None:
    before = [{"destination": "a.example.com"}]
    after = [{"destination": "a.example.com"}, {"destination": "b.example.com"}, {"destination": "c.example.com"}]

    diff = diff_destinations(before, after)

    assert diff == {"new_destinations": ["b.example.com", "c.example.com"], "event_count_delta": 2}


def test_diff_destinations_empty_when_no_new_destinations() -> None:
    before = [{"destination": "a.example.com"}]
    after = [{"destination": "a.example.com"}]

    assert diff_destinations(before, after) == {"new_destinations": [], "event_count_delta": 0}


def test_observe_reports_observed_true_and_stores_before_after_summaries(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    before = [{"destination": "a.example.com", "bytes": 10}]
    after = [{"destination": "a.example.com", "bytes": 10}, {"destination": "evil.example.com", "bytes": 999}]

    result = observe(before, after, evidence=store)

    assert result.kind == "network"
    assert result.status == ObservationStatus.observed_true
    assert result.value == {"new_destinations": ["evil.example.com"], "event_count_delta": 1}
    assert result.evidence_digest is not None
    evidence_bytes = store.get(result.evidence_digest)
    assert b"evil.example.com" in evidence_bytes


def test_observe_reports_observed_false_when_no_new_destinations(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    events = [{"destination": "a.example.com"}]

    result = observe(events, events, evidence=store)

    assert result.status == ObservationStatus.observed_false
    assert result.value == {"new_destinations": [], "event_count_delta": 0}


def test_observe_consumes_one_shot_iterables_correctly(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    before_events = [{"destination": "a.example.com"}]
    after_events = [{"destination": "a.example.com"}, {"destination": "b.example.com"}]

    result = observe((e for e in before_events), (e for e in after_events), evidence=store)

    assert result.status == ObservationStatus.observed_true
    assert result.value == {"new_destinations": ["b.example.com"], "event_count_delta": 1}


def test_observe_is_inconclusive_on_event_missing_destination(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    before: list[dict[str, object]] = []
    after = [{"bytes": 10}]  # missing required "destination" key

    result = observe(before, after, evidence=store, detail="probe step 9")

    assert result.status == ObservationStatus.inconclusive
    assert result.value is None
    assert result.evidence_digest is None
    assert result.detail is not None and result.detail.startswith("probe step 9: ")
