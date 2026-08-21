"""ProbePlan intermediate representation.

The planner emits this typed IR; it never calls tools directly. Every model
forbids extra fields, so an unrecognized property is a validation error, not
silently ignored data.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from babi.canon import Json


class ProbePrimitive(str, Enum):
    DISCOVER = "DISCOVER"
    INSTANTIATE = "INSTANTIATE"
    CALL_ONCE = "CALL_ONCE"
    READ_BACK = "READ_BACK"
    REPEAT = "REPEAT"
    RETRY_AFTER_INTERRUPT = "RETRY_AFTER_INTERRUPT"
    PARALLEL_DOUBLE_FIRE = "PARALLEL_DOUBLE_FIRE"
    VERIFY_WITH_TIMING = "VERIFY_WITH_TIMING"
    PERTURB_AND_UNDO = "PERTURB_AND_UNDO"
    BOUNDARY_MUTATION_FUZZ = "BOUNDARY_MUTATION_FUZZ"
    CROSS_CALL_CONSISTENCY = "CROSS_CALL_CONSISTENCY"
    PRECONDITION_REVOCATION = "PRECONDITION_REVOCATION"


class RationaleProvenance(str, Enum):
    """Where a plan's dependency edge came from. There is no third,
    handwritten option -- that is the point: an edge with no entry here is
    unjustified by construction, not merely unlabeled."""

    SCHEMA_OVERLAP = "schema-overlap"
    MODEL_PASS = "model-pass"


class DependencyEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_step: str
    to_step: str


class RationaleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge: DependencyEdge
    provenance: RationaleProvenance
    detail: str | None = None


class ProbeStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    primitive: ProbePrimitive
    tool_subject: str
    depends_on: list[str] = Field(default_factory=list)
    parameters: dict[str, Json] = Field(default_factory=dict)


class ProbePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_digest: str
    prerequisites: list[str] = Field(default_factory=list)
    generated_resources: list[str] = Field(default_factory=list)
    steps: list[ProbeStep] = Field(default_factory=list)
    observers: list[str] = Field(default_factory=list)
    cleanup_budget: int = Field(ge=0)
    stop_conditions: list[str] = Field(default_factory=list)
    rationale_evidence: list[RationaleEntry] = Field(default_factory=list)
