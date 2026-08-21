"""Pydantic models for the in-toto Statement v1 wrapping a
`https://behavioral-abi.dev/contract/v0` behavioral contract predicate.

Mirrors `research/signing/contract-schema-v0.json` (a draft, not-yet-
published predicate schema) field-for-field, with two deliberate,
documented departures where the draft's letter would force either a
fabricated value or an unrepresentable state:

- `Observed.idempotent`/`Observed.reversible` add an `"unknown"` literal.
  The draft's `reversible` enum already includes `"unknown"`; `idempotent`
  does not, which looks like an oversight rather than a deliberate
  asymmetry, since both properties come from the same four-state
  `PropertyStatus` evidence model and both need a way to say "no
  conclusive claim exists" without either fabricating a boolean or
  emitting a JSON `null` a `type: ["boolean", "string"]` field can't
  actually hold. `idempotent` follows `reversible`'s own precedent.
- `predicate.provenance`: an additive namespace (the draft schema sets no
  `additionalProperties: false` anywhere) carrying everything
  IMPLEMENTATION-PLAN.md's "Also record" list asks for beyond the draft's
  minimum predicate: observer coverage, run count, clean-state
  independence, protocol/SDK versions, planner model/prompt digest, and a
  reproduction-count-only confidence score.

Every model is `extra="forbid"`, matching this project's convention
(`babi.plan`, `babi.target`, `babi.manifest`): a field neither this module
nor its consumers declared is a bug, not silently-ignored data.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from babi.canon import Json

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://behavioral-abi.dev/contract/v0"


class SubjectDigest(BaseModel):
    """in-toto `DigestSet`: bare hex under an algorithm-named key, per
    https://github.com/in-toto/attestation/blob/main/spec/v1/digest_set.md
    -- never `babi`'s own internal `"sha256:<hex>"`-prefixed convention,
    which would be redundant with the `sha256` key itself."""

    model_config = ConfigDict(extra="forbid")

    sha256: str
    toolVersion: str | None = None


class Subject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    digest: SubjectDigest


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: Literal["mcp", "rest", "graphql", "grpc", "other"]
    name: str
    input_schema_digest: str
    vendor_declared: dict[str, Json] = Field(default_factory=dict)


class SideEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    target: str | None = None
    confidence: Literal["observed", "inferred"]


class Consistency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Literal["strong", "eventual", "unknown"] = "unknown"
    observed_staleness_ms_p50: float | None = None
    observed_staleness_ms_p99: float | None = None


class Observed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotent: bool | Literal["conditional", "unknown"]
    reversible: bool | Literal["partial", "unknown"]
    side_effects: list[SideEffect] = Field(default_factory=list)
    consistency: Consistency = Field(default_factory=Consistency)


class Precondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    confidence: Literal["observed", "inferred"]


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str | None = None
    sha256: str


class Prober(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: str


class ClientSdk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    language: str


class CleanState(BaseModel):
    """Empirical evidence that the runs backing this contract started from
    independent clean state -- not a description of the isolation
    mechanism itself (that lives in the sandbox manifest, out of a
    contract's scope), but the `clean_state_id` set those runs actually
    recorded, so a reader can check independence rather than take it on
    faith."""

    model_config = ConfigDict(extra="forbid")

    run_count: int = Field(ge=0)
    distinct_clean_state_ids: int = Field(ge=0)
    clean_state_ids: list[str] = Field(default_factory=list)


class Planner(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    prompt_digest: str | None = None


class Confidence(BaseModel):
    """Confidence derived only from reproduction count (IMPLEMENTATION-
    PLAN.md: "never an opaque model score"): the fewest runs any one
    classified property claim was reproduced across (`reproduced_runs`)
    over the most runs any one claim was reproduced across (`total_runs`)
    -- both pulled directly from `PropertyClaim.runs`, never from
    `len(history_digests)`, which counts independent clean-state runs and
    is a different, incommensurable unit (one history can itself supply
    several readings of the same claim, e.g. via a `REPEAT` primitive)."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["reproduction_count"] = "reproduction_count"
    reproduced_runs: int = Field(ge=0)
    total_runs: int = Field(ge=0)
    value: float = Field(ge=0.0, le=1.0)


class ClaimRecord(BaseModel):
    """One `babi.classify.PropertyClaim`, carried into the contract
    verbatim so no classifier fidelity is lost to the two headline
    `observed.idempotent`/`observed.reversible` summary fields."""

    model_config = ConfigDict(extra="forbid")

    property: str
    status: str
    runs: int = Field(ge=1)
    scope: str
    detail: str | None = None
    evidence_digests: list[str] = Field(default_factory=list)


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observer_coverage: list[str] = Field(default_factory=list)
    run_count: int = Field(ge=0)
    clean_state: CleanState
    protocol_revision: str
    package_or_endpoint: str
    client_sdk: ClientSdk | None = None
    planner: Planner = Field(default_factory=Planner)
    confidence: Confidence
    discrepancy_classes: list[str] = Field(default_factory=list)
    claims: list[ClaimRecord] = Field(default_factory=list)


class ContractPredicate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Operation
    observed: Observed
    preconditions: list[Precondition] = Field(default_factory=list)
    evidence_ref: EvidenceRef
    verified_at: str
    prober: Prober
    supersedes: str | None = None
    provenance: Provenance


class Statement(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type_: Literal["https://in-toto.io/Statement/v1"] = Field(default=STATEMENT_TYPE, alias="_type")
    subject: list[Subject] = Field(min_length=1)
    predicateType: Literal["https://behavioral-abi.dev/contract/v0"] = PREDICATE_TYPE
    predicate: ContractPredicate


__all__ = [
    "PREDICATE_TYPE",
    "STATEMENT_TYPE",
    "ClaimRecord",
    "CleanState",
    "ClientSdk",
    "Confidence",
    "Consistency",
    "ContractPredicate",
    "EvidenceRef",
    "Observed",
    "Operation",
    "Planner",
    "Precondition",
    "Prober",
    "Provenance",
    "SideEffect",
    "Statement",
    "Subject",
    "SubjectDigest",
]
