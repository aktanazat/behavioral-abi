"""ContractEmitter: builds a deterministic in-toto Statement v1 behavioral
contract from a classification, its backing discovery bundle, and the
probe histories that produced it.

Pure function of its inputs plus whatever those inputs' digests resolve to
in the evidence store -- never wall-clock time, an opaque model score, or
a branch keyed on a tool/operation name. Calling `emit()` twice with the
same arguments against the same evidence store produces byte-identical
output (RUBRIC.md Phase 1 exit: "the same target produces the same digest
... twice").
"""

from __future__ import annotations

import json
from importlib.metadata import version as _pkg_version

from babi.canon import Json
from babi.canon import digest as canon_digest
from babi.classify import BehaviorClassification, DiscrepancyClass, PropertyClaim, PropertyStatus
from babi.evidence import EvidenceStore
from babi.history import ProbeHistory

from babi.contract.schema import (
    ClaimRecord,
    CleanState,
    ClientSdk,
    Confidence,
    Consistency,
    ContractPredicate,
    EvidenceRef,
    Observed,
    Operation,
    Planner,
    Prober,
    Provenance,
    SideEffect,
    Statement,
    Subject,
    SubjectDigest,
)

PROBER_ID = "behavioral-abi-prober"

_RETRY_PROPERTY = "idempotent"
_KNOWN_MCP_TRANSPORTS = frozenset({"stdio", "streamable-http"})


class ContractError(ValueError):
    """`emit()` was given inputs that cannot produce a faithful contract:
    a mismatched or incomplete discovery bundle, no backing history, or an
    undereferenceable evidence digest."""


def _bare_hex(value: str) -> str:
    """Strip babi's internal `"sha256:<hex>"` prefix (if present) down to
    the bare hex an in-toto `DigestSet` wants; validate either way."""
    _, sep, hexpart = value.partition(":")
    candidate = hexpart if sep else value
    if len(candidate) != 64 or any(ch not in "0123456789abcdef" for ch in candidate.lower()):
        raise ContractError(f"not a sha256 hex digest: {value!r}")
    return candidate.lower()


def _enum_value(value: object) -> str:
    """`.value` for an Enum member, else the object itself -- never
    `str(member)`, which for a `(str, Enum)` mixin renders
    `"ClassName.member"` instead of the member's own string value."""
    return value.value if hasattr(value, "value") else str(value)


def _lookup_tool_definition(discovery_bundle: dict[str, Json], tool_name: str) -> dict[str, Json]:
    catalog = discovery_bundle.get("tools_list")
    if isinstance(catalog, list):
        for tool in catalog:
            if isinstance(tool, dict) and tool.get("name") == tool_name:
                return tool
    raise ContractError(f"tool {tool_name!r} not present in discovery_bundle['tools_list']")


def _protocol_for(discovery_bundle: dict[str, Json]) -> str:
    return "mcp" if discovery_bundle.get("transport") in _KNOWN_MCP_TRANSPORTS else "other"


def _observed_value(claim: PropertyClaim | None) -> bool | str:
    if claim is None:
        return "unknown"
    status = _enum_value(claim.status)
    if status == PropertyStatus.observed_true.value:
        return True
    if status == PropertyStatus.observed_false.value:
        return False
    return "unknown"


def _side_effects(classification: BehaviorClassification) -> list[SideEffect]:
    classes = {_enum_value(cls) for cls in classification.discrepancy_classes}
    if DiscrepancyClass.undeclared_side_effect.value in classes:
        return [SideEffect(kind="undeclared_side_effect", confidence="observed")]
    return []


def _client_sdk(discovery_bundle: dict[str, Json]) -> ClientSdk | None:
    raw = discovery_bundle.get("client_sdk")
    if not isinstance(raw, dict):
        return None
    name, version, language = raw.get("name"), raw.get("version"), raw.get("language")
    if isinstance(name, str) and isinstance(version, str) and isinstance(language, str):
        return ClientSdk(name=name, version=version, language=language)
    return None


class ContractEmitter:
    """Emits `https://behavioral-abi.dev/contract/v0` in-toto Statements.
    Needs an `EvidenceStore` because faithfully reporting observer
    coverage, run count, clean-state independence, `verified_at`, and
    planner provenance requires dereferencing the `ProbeHistory` (and,
    transitively, `plan/v0` envelope) blobs `history_digests` names --
    none of that is reconstructable from `classification`/
    `discovery_bundle` alone."""

    def __init__(self, evidence: EvidenceStore, *, prober_version: str | None = None) -> None:
        self._evidence = evidence
        self._prober_version = prober_version or _pkg_version("babi")

    def emit(
        self,
        classification: BehaviorClassification,
        discovery_bundle: dict[str, Json],
        history_digests: list[str],
        evidence_digest: str,
        supersedes: str | None = None,
    ) -> dict[str, Json]:
        if not history_digests:
            raise ContractError("emit() requires at least one history digest -- a contract needs evidence")

        if discovery_bundle.get("tool_subjects", {}).get(classification.tool_name) != classification.tool_subject:
            raise ContractError(
                f"discovery_bundle.tool_subjects[{classification.tool_name!r}] does not match "
                f"classification.tool_subject={classification.tool_subject!r} -- refusing to emit a "
                "contract for mismatched identity"
            )

        tool_def = _lookup_tool_definition(discovery_bundle, classification.tool_name)
        input_schema = tool_def.get("inputSchema")
        if input_schema is None:
            raise ContractError(f"tool {classification.tool_name!r} has no inputSchema in discovery_bundle")

        histories = [self._load_history(digest) for digest in history_digests]

        claims_by_property: dict[str, PropertyClaim] = {}
        for claim in classification.claims:
            # First claim per property wins; a well-formed classification carries at most
            # one claim per (property, scope) pair going into emit(), and this contract's
            # headline `observed.*` fields are per-property summaries, not per-scope ones.
            claims_by_property.setdefault(claim.property, claim)

        operation = Operation(
            protocol=_protocol_for(discovery_bundle),
            name=classification.tool_name,
            input_schema_digest=canon_digest(input_schema),
            vendor_declared=dict(tool_def.get("annotations") or {}),
        )
        observed = Observed(
            idempotent=_observed_value(claims_by_property.get(_RETRY_PROPERTY)),
            reversible=_observed_value(claims_by_property.get("reversible")),
            side_effects=_side_effects(classification),
            consistency=Consistency(),
        )

        run_count = len(history_digests)
        observer_coverage = sorted(
            {
                result.kind
                for history in histories
                for event in history.events
                for result in event.observer_results
            }
        )
        clean_state_ids = sorted({history.clean_state_id for history in histories})
        verified_at = _latest_timestamp(histories)
        planner_model, planner_prompt_digest = self._planner_info(histories)

        claim_run_counts = [claim.runs for claim in classification.claims]
        reproduced_runs = min(claim_run_counts, default=0)
        strongest_runs = max(claim_run_counts, default=0)
        confidence = Confidence(
            reproduced_runs=reproduced_runs,
            total_runs=strongest_runs,
            value=(reproduced_runs / strongest_runs) if strongest_runs else 0.0,
        )

        provenance = Provenance(
            observer_coverage=observer_coverage,
            run_count=run_count,
            clean_state=CleanState(
                run_count=run_count,
                distinct_clean_state_ids=len(clean_state_ids),
                clean_state_ids=clean_state_ids,
            ),
            protocol_revision=str(discovery_bundle.get("negotiated_protocol_revision") or ""),
            package_or_endpoint=str(discovery_bundle.get("package_or_endpoint") or ""),
            client_sdk=_client_sdk(discovery_bundle),
            planner=Planner(model=planner_model, prompt_digest=planner_prompt_digest),
            confidence=confidence,
            discrepancy_classes=sorted({_enum_value(cls) for cls in classification.discrepancy_classes}),
            claims=[
                ClaimRecord(
                    property=claim.property,
                    status=_enum_value(claim.status),
                    runs=claim.runs,
                    scope=claim.scope,
                    detail=claim.detail,
                    evidence_digests=list(claim.evidence_digests),
                )
                for claim in sorted(classification.claims, key=lambda c: (c.property, c.scope))
            ],
        )

        predicate = ContractPredicate(
            operation=operation,
            observed=observed,
            preconditions=[],
            evidence_ref=EvidenceRef(uri=f"cas://sha256/{_bare_hex(evidence_digest)}", sha256=evidence_digest),
            verified_at=verified_at,
            prober=Prober(id=PROBER_ID, version=self._prober_version),
            supersedes=supersedes,
            provenance=provenance,
        )

        target_name = discovery_bundle.get("run", {}).get("target_name") or discovery_bundle.get(
            "package_or_endpoint", ""
        )
        runtime_digests = discovery_bundle.get("runtime_digests") or {}
        tool_version = runtime_digests.get("npm_package_version") or discovery_bundle.get("package_or_endpoint")

        statement = Statement(
            subject=[
                Subject(
                    name=f"mcp:{target_name}/{classification.tool_name}",
                    digest=SubjectDigest(
                        sha256=_bare_hex(classification.tool_subject),
                        toolVersion=tool_version if isinstance(tool_version, str) else None,
                    ),
                )
            ],
            predicate=predicate,
        )
        return statement.model_dump(by_alias=True, mode="json", exclude_none=True)

    def _load_history(self, digest: str) -> ProbeHistory:
        try:
            raw = self._evidence.get(digest)
        except (FileNotFoundError, ValueError) as error:
            raise ContractError(f"could not load history {digest!r} from evidence store: {error}") from error
        return ProbeHistory.model_validate(json.loads(raw))

    def _planner_info(self, histories: list[ProbeHistory]) -> tuple[str | None, str | None]:
        """Best-effort `model`/`prompt_digest` from each history's
        `plan/v0` evidence envelope. Never fabricated: a run whose plan
        envelope is missing or lacks these keys just contributes nothing;
        disagreement across runs is surfaced (sorted, joined), not
        silently resolved to one arbitrary value."""
        models: set[str] = set()
        prompt_digests: set[str] = set()
        for history in histories:
            try:
                raw = json.loads(self._evidence.get(history.plan_digest))
            except (FileNotFoundError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            model = raw.get("model")
            if isinstance(model, str) and model:
                models.add(model)
            prompt_digest = raw.get("prompt_digest")
            if isinstance(prompt_digest, str) and prompt_digest:
                prompt_digests.add(prompt_digest)
        return _single_or_joined(models), _single_or_joined(prompt_digests)


def _single_or_joined(values: set[str]) -> str | None:
    if not values:
        return None
    if len(values) == 1:
        return next(iter(values))
    return ", ".join(sorted(values))


def _latest_timestamp(histories: list[ProbeHistory]) -> str:
    timestamps = [history.finished_at for history in histories if history.finished_at]
    if not timestamps:
        raise ContractError("no backing history provides a finished_at timestamp")
    return max(timestamps)


__all__ = ["ContractEmitter", "ContractError", "PROBER_ID"]
