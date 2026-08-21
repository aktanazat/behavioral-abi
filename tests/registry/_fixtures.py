"""Shared, minimal contract-record builder for registry tests.

Deliberately built as a raw dict rather than through `ContractEmitter`:
registry tests exercise the registry's own storage contract (parse a
well-formed Statement -> extract subject/evidence_digest/verified_at/
provenance -> index it), not contract emission, which `tests/contract/`
already covers in depth. Not a test file itself (`_` prefix keeps pytest
from collecting it).
"""

from __future__ import annotations

from babi.canon import Json

TOOL_SUBJECT = "sha256:" + "ab" * 32
OTHER_TOOL_SUBJECT = "sha256:" + "cd" * 32


def make_record(
    *,
    tool_subject: str = TOOL_SUBJECT,
    tool_name: str = "create_widget",
    verified_at: str = "2026-08-20T00:00:02Z",
    idempotent_status: str = "observed_true",
    destructive: bool = False,
    open_world: bool = False,
    observer_coverage: list[str] | None = None,
    evidence_digest: str = "sha256:" + "ee" * 32,
    supersedes: str | None = None,
    package_or_endpoint: str = "@example/widgets-mcp",
    protocol_revision: str = "2026-07-28",
    include_rekor_entry: bool = True,
    extra_claims: list[dict] | None = None,
) -> dict[str, Json]:
    """A well-formed `{"statement": ..., "dsse_envelope": ..., "cosign_bundle": ...}`
    record, shaped exactly like `ContractEmitter.emit()`'s output plus a
    signing wrapper -- everything `Registry.insert_contract` reads."""
    claims = [
        {
            "property": "idempotent",
            "status": idempotent_status,
            "runs": 2,
            "scope": "REPEAT",
            "detail": None,
            "evidence_digests": [],
        },
        *(extra_claims or []),
    ]
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": f"mcp:widgets/{tool_name}", "digest": {"sha256": tool_subject.removeprefix("sha256:")}}],
        "predicateType": "https://behavioral-abi.dev/contract/v0",
        "predicate": {
            "operation": {
                "protocol": "mcp",
                "name": tool_name,
                "input_schema_digest": "sha256:" + "11" * 32,
                "vendor_declared": {"destructiveHint": destructive, "openWorldHint": open_world},
            },
            "observed": {
                "idempotent": idempotent_status == "observed_true",
                "reversible": "unknown",
                "side_effects": [],
                "consistency": {"model": "unknown"},
            },
            "preconditions": [],
            "evidence_ref": {"uri": f"cas://sha256/{evidence_digest.removeprefix('sha256:')}", "sha256": evidence_digest},
            "verified_at": verified_at,
            "prober": {"id": "behavioral-abi-prober", "version": "0.1.0-test"},
            "provenance": {
                "observer_coverage": observer_coverage if observer_coverage is not None else ["read_back"],
                "run_count": 1,
                "clean_state": {"run_count": 1, "distinct_clean_state_ids": 1, "clean_state_ids": ["clean-1"]},
                "protocol_revision": protocol_revision,
                "package_or_endpoint": package_or_endpoint,
                "planner": {},
                "confidence": {"method": "reproduction_count", "reproduced_runs": 2, "total_runs": 2, "value": 1.0},
                "discrepancy_classes": [],
                "claims": claims,
            },
        },
    }
    if supersedes is not None:
        statement["predicate"]["supersedes"] = supersedes

    cosign_bundle: dict[str, Json] | None = {"messageSignature": {"signature": "c2ln"}}
    if include_rekor_entry:
        cosign_bundle["verificationMaterial"] = {"tlogEntries": [{"logIndex": "424242"}]}

    return {
        "statement": statement,
        "dsse_envelope": {"payload": "eyJ9", "payloadType": "application/vnd.in-toto+json", "signatures": [{"sig": "c2ln"}]},
        "cosign_bundle": cosign_bundle,
    }
