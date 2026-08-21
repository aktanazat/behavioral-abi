"""Exact-digest retry/action policy: `deny_retry | require_read_back |
require_approval | allow | unknown`.

`decide()` is a pure function over already-gathered facts (never touches
the database itself -- `Registry.policy()` does the gathering); every
branch is over generic ABI vocabulary the classifier and contract emitter
already produce (the `idempotent` property, vendor-declared
`destructiveHint`/`openWorldHint`, `read_back` observer coverage) and
never a tool/provider name (RUBRIC.md "No provider logic").

Decision priority, most to least authoritative:

1. missing / identity-mismatched / stale contract -> `unknown` (never
   trust a contract that isn't verifiably about the exact subject asked
   about, and never let an old contract answer a fresh question forever)
2. a direct `observed_false` idempotency claim -> `deny_retry` (the
   strongest signal available: execution itself proved a repeat is unsafe)
3. vendor-declared destructive or open-world -> `require_approval` (an
   independent safety gate -- idempotent does not mean "safe without
   oversight", it only means "repeating it adds no *extra* effect")
4. no conclusive idempotency claim, but a read-back oracle exists ->
   `require_read_back` (an actionable middle ground between "we know
   nothing" and "we have a firm empirical answer")
5. no conclusive claim and no read-back oracle -> `unknown`
6. a direct `observed_true` idempotency claim -> `allow`
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from babi.canon import Json

DEFAULT_MAX_AGE = timedelta(days=30)
DEFAULT_INTENT = "retry"

_RETRY_PROPERTY = "idempotent"
_OBSERVED_TRUE = "observed_true"
_OBSERVED_FALSE = "observed_false"
_READ_BACK_KIND = "read_back"

_DECISIONS = frozenset({"deny_retry", "require_read_back", "require_approval", "allow", "unknown"})


@dataclass(frozen=True, slots=True)
class PolicyResult:
    decision: str
    basis: str
    contract_digest: str | None
    verified_at: str | None
    expires_at: str | None
    reason: str

    def __post_init__(self) -> None:
        if self.decision not in _DECISIONS:
            raise ValueError(f"not a policy decision: {self.decision!r}")

    def to_json(self) -> dict[str, Json]:
        return {
            "decision": self.decision,
            "basis": self.basis,
            "contract_digest": self.contract_digest,
            "verified_at": self.verified_at,
            "expires_at": self.expires_at,
            "reason": self.reason,
        }


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def decide(
    *,
    intent: str,
    subject_digest: str,
    observation_subject_digest: str | None,
    contract_digest: str | None,
    verified_at: str | None,
    claim_status: str | None,
    vendor_declared: dict[str, Json],
    observer_coverage: list[str],
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_MAX_AGE,
) -> PolicyResult:
    now = now or datetime.now(timezone.utc)

    if contract_digest is None or verified_at is None:
        return PolicyResult(
            decision="unknown",
            basis="missing",
            contract_digest=None,
            verified_at=None,
            expires_at=None,
            reason=f"no contract on file for subject {subject_digest}",
        )

    if observation_subject_digest != subject_digest:
        return PolicyResult(
            decision="unknown",
            basis="identity_mismatch",
            contract_digest=contract_digest,
            verified_at=verified_at,
            expires_at=None,
            reason="the stored contract's subject digest does not match the requested subject digest",
        )

    try:
        verified_dt = _parse_iso(verified_at)
    except ValueError:
        return PolicyResult(
            decision="unknown",
            basis="malformed_timestamp",
            contract_digest=contract_digest,
            verified_at=verified_at,
            expires_at=None,
            reason=f"contract verified_at {verified_at!r} is not a parseable timestamp",
        )
    expires_at = (verified_dt + max_age).isoformat()
    if now > verified_dt + max_age:
        return PolicyResult(
            decision="unknown",
            basis="stale",
            contract_digest=contract_digest,
            verified_at=verified_at,
            expires_at=expires_at,
            reason=f"contract verified_at {verified_at} is older than the {max_age} freshness window",
        )

    if intent == DEFAULT_INTENT and claim_status == _OBSERVED_FALSE:
        return PolicyResult(
            decision="deny_retry",
            basis=claim_status,
            contract_digest=contract_digest,
            verified_at=verified_at,
            expires_at=expires_at,
            reason="repeated identical calls were observed to produce additional effect -- retrying is unsafe",
        )

    if vendor_declared.get("destructiveHint") is True or vendor_declared.get("openWorldHint") is True:
        return PolicyResult(
            decision="require_approval",
            basis=claim_status or "unknown",
            contract_digest=contract_digest,
            verified_at=verified_at,
            expires_at=expires_at,
            reason="operation is vendor-declared destructive or open-world -- require human approval",
        )

    if claim_status not in (_OBSERVED_TRUE, _OBSERVED_FALSE):
        if _READ_BACK_KIND in observer_coverage:
            return PolicyResult(
                decision="require_read_back",
                basis=claim_status or "missing",
                contract_digest=contract_digest,
                verified_at=verified_at,
                expires_at=expires_at,
                reason=(
                    f"no conclusive {_RETRY_PROPERTY!r} observation backs intent {intent!r}, but a "
                    "read-back oracle is available -- verify current state before proceeding"
                ),
            )
        return PolicyResult(
            decision="unknown",
            basis=claim_status or "missing",
            contract_digest=contract_digest,
            verified_at=verified_at,
            expires_at=expires_at,
            reason=f"no conclusive {_RETRY_PROPERTY!r} observation backs intent {intent!r}",
        )

    if intent == DEFAULT_INTENT and claim_status == _OBSERVED_TRUE:
        return PolicyResult(
            decision="allow",
            basis=claim_status,
            contract_digest=contract_digest,
            verified_at=verified_at,
            expires_at=expires_at,
            reason="repeated identical calls were observed to have no additional effect",
        )

    return PolicyResult(
        decision="unknown",
        basis=claim_status or "missing",
        contract_digest=contract_digest,
        verified_at=verified_at,
        expires_at=expires_at,
        reason=f"no policy rule matched intent {intent!r}",
    )


__all__ = ["DEFAULT_INTENT", "DEFAULT_MAX_AGE", "PolicyResult", "decide"]
