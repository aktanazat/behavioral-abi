"""Exact-digest retry policy: `decide()` unit tests for every branch of
the priority chain, plus `Registry.policy()` integration tests proving
non-idempotent => deny_retry and identity mismatch => unknown end to end
through real stored contracts."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from babi.registry.policy import decide
from babi.registry.store import Registry

from _fixtures import OTHER_TOOL_SUBJECT, TOOL_SUBJECT, make_record

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)
VERIFIED_AT = "2026-08-19T00:00:00+00:00"


def _decide(**overrides):
    defaults = dict(
        intent="retry",
        subject_digest=TOOL_SUBJECT,
        observation_subject_digest=TOOL_SUBJECT,
        contract_digest="sha256:" + "ff" * 32,
        verified_at=VERIFIED_AT,
        claim_status="observed_true",
        vendor_declared={},
        observer_coverage=[],
        now=NOW,
    )
    defaults.update(overrides)
    return decide(**defaults)


# -- missing / identity mismatch / stale -> unknown ------------------------------


def test_missing_contract_is_unknown() -> None:
    result = _decide(observation_subject_digest=None, contract_digest=None, verified_at=None, claim_status=None)
    assert result.decision == "unknown"
    assert result.basis == "missing"


def test_identity_mismatch_is_unknown() -> None:
    result = _decide(observation_subject_digest=OTHER_TOOL_SUBJECT)
    assert result.decision == "unknown"
    assert result.basis == "identity_mismatch"


def test_stale_contract_is_unknown() -> None:
    result = _decide(now=NOW + timedelta(days=31), max_age=timedelta(days=30))
    assert result.decision == "unknown"
    assert result.basis == "stale"


def test_fresh_contract_within_max_age_is_not_stale() -> None:
    result = _decide(now=NOW + timedelta(days=29), max_age=timedelta(days=30))
    assert result.decision == "allow"


def test_malformed_verified_at_is_unknown() -> None:
    result = _decide(verified_at="not-a-timestamp")
    assert result.decision == "unknown"
    assert result.basis == "malformed_timestamp"


# -- deny_retry -------------------------------------------------------------------


def test_observed_non_idempotent_denies_retry() -> None:
    result = _decide(claim_status="observed_false")
    assert result.decision == "deny_retry"
    assert result.basis == "observed_false"


def test_deny_retry_only_applies_to_retry_intent() -> None:
    result = _decide(claim_status="observed_false", intent="act")
    assert result.decision != "deny_retry"


def test_deny_retry_takes_priority_over_destructive_hint() -> None:
    """A hard empirical no beats a heuristic caution flag."""
    result = _decide(claim_status="observed_false", vendor_declared={"destructiveHint": True})
    assert result.decision == "deny_retry"


# -- require_approval ---------------------------------------------------------------


def test_destructive_hint_requires_approval_even_when_idempotent() -> None:
    """Idempotent does not mean safe without oversight -- it only means
    repeating it adds no *extra* effect."""
    result = _decide(claim_status="observed_true", vendor_declared={"destructiveHint": True})
    assert result.decision == "require_approval"


def test_open_world_hint_requires_approval() -> None:
    result = _decide(claim_status="observed_true", vendor_declared={"openWorldHint": True})
    assert result.decision == "require_approval"


def test_destructive_false_does_not_require_approval() -> None:
    result = _decide(claim_status="observed_true", vendor_declared={"destructiveHint": False})
    assert result.decision == "allow"


# -- require_read_back vs unknown ----------------------------------------------------


def test_inconclusive_with_read_back_oracle_requires_read_back() -> None:
    result = _decide(claim_status="inconclusive", observer_coverage=["read_back"])
    assert result.decision == "require_read_back"


def test_inconclusive_without_read_back_oracle_is_unknown() -> None:
    result = _decide(claim_status="inconclusive", observer_coverage=[])
    assert result.decision == "unknown"
    assert result.basis == "inconclusive"


def test_missing_claim_status_without_read_back_is_unknown() -> None:
    result = _decide(claim_status=None, observer_coverage=[])
    assert result.decision == "unknown"


def test_not_applicable_claim_status_is_treated_as_inconclusive() -> None:
    result = _decide(claim_status="not_applicable", observer_coverage=["read_back"])
    assert result.decision == "require_read_back"


# -- allow ----------------------------------------------------------------------------


def test_observed_idempotent_allows_retry() -> None:
    result = _decide(claim_status="observed_true")
    assert result.decision == "allow"
    assert result.basis == "observed_true"


def test_observed_true_with_non_retry_intent_falls_to_unknown() -> None:
    result = _decide(claim_status="observed_true", intent="act")
    assert result.decision == "unknown"


# -- PolicyResult shape -----------------------------------------------------------------


def test_policy_result_to_json_shape() -> None:
    result = _decide(claim_status="observed_false")
    doc = result.to_json()
    assert set(doc) == {"decision", "basis", "contract_digest", "verified_at", "expires_at", "reason"}
    assert doc["decision"] == "deny_retry"
    assert isinstance(doc["reason"], str) and doc["reason"]


def test_policy_result_rejects_unknown_decision_string() -> None:
    import pytest

    from babi.registry.policy import PolicyResult

    with pytest.raises(ValueError):
        PolicyResult(
            decision="maybe", basis="x", contract_digest=None, verified_at=None, expires_at=None, reason="x"
        )


# -- Registry.policy() integration: non-idempotent => deny_retry --------------------


def _registry(tmp_path: Path) -> Registry:
    from babi.evidence import EvidenceStore

    return Registry(tmp_path / "registry.sqlite3", EvidenceStore(tmp_path / "evidence"))


def test_registry_policy_non_idempotent_denies_retry(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.insert_contract(make_record(idempotent_status="observed_false"))
    result = registry.policy(TOOL_SUBJECT, intent="retry")
    assert result.decision == "deny_retry"
    registry.close()


def test_registry_policy_idempotent_allows_retry(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.insert_contract(make_record(idempotent_status="observed_true"))
    result = registry.policy(TOOL_SUBJECT, intent="retry")
    assert result.decision == "allow"
    registry.close()


def test_registry_policy_unknown_subject_is_unknown(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    result = registry.policy("sha256:" + "00" * 32, intent="retry")
    assert result.decision == "unknown"
    assert result.basis == "missing"
    registry.close()


def test_registry_policy_destructive_requires_approval_end_to_end(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.insert_contract(make_record(idempotent_status="observed_true", destructive=True))
    result = registry.policy(TOOL_SUBJECT, intent="retry")
    assert result.decision == "require_approval"
    registry.close()


def test_registry_policy_identity_mismatch_is_unknown_end_to_end(tmp_path: Path) -> None:
    """The stored contract body's own embedded subject digest no longer
    matches the subject_digest the observation is filed under (simulated
    CAS/index drift) -- registry.policy() must catch this and refuse to
    answer rather than trust a mismatched entry."""
    registry = _registry(tmp_path)
    record = make_record(tool_subject=TOOL_SUBJECT)
    contract_digest = registry.insert_contract(record)
    assert registry.policy(TOOL_SUBJECT, intent="retry").decision == "allow"  # sanity: matches before tampering

    tampered = json.loads(json.dumps(record))
    tampered["statement"]["subject"][0]["digest"]["sha256"] = OTHER_TOOL_SUBJECT.removeprefix("sha256:")
    registry._evidence.path(contract_digest).write_text(json.dumps(tampered))

    result = registry.policy(TOOL_SUBJECT, intent="retry")
    assert result.decision == "unknown"
    assert result.basis == "identity_mismatch"
    registry.close()
