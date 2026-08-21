"""Constrained model-pass abstraction.

`ModelPlanner` is a thin, provider-agnostic wrapper: it builds a
deterministic prompt from the tool catalog, the deterministic pass's
candidates, and sandbox constraints; runs a caller-injected subprocess
command with that prompt on stdin; and validates the command's stdout as
strict JSON that must itself validate as a `ProbePlan`. There is no
embedded model provider, SDK, or API key anywhere in this module -- the
model identity and how to actually reach it are entirely the caller's
concern (IMPLEMENTATION-PLAN.md: "The model pass receives only the tool
catalog, schemas, descriptions, sandbox constraints, and deterministic
candidates. It ... returns schema-valid JSON. Temperature is zero. Persist
the prompt, model identifier, and response.").

This module never adds a dependency edge the deterministic pass
(`dependencies.py`) did not already propose as a candidate; it only ranks
or selects among them (`RationaleProvenance.MODEL_PASS` on the resulting
plan, if the caller chooses to trust the returned ranking) or supplies
argument values the deterministic pass left as a placeholder.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from babi.canon import Json
from babi.evidence import EvidenceStore
from babi.manifest import SandboxManifest
from babi.plan import ProbePlan, ProbeStep
from babi.planner.dependencies import DeterministicCandidates

PLAN_KIND = "plan/v0"


class ModelInvocationError(RuntimeError):
    """The injected command itself failed to run (nonzero exit, spawn
    failure, timeout) -- distinct from a response that ran fine but was
    malformed (`MalformedModelResponseError`)."""


class MalformedModelResponseError(RuntimeError):
    """The command's stdout was not parseable JSON, was not a JSON object,
    or did not validate as a `ProbePlan`. Never partially accepted or
    silently coerced -- this is the rejection path IMPLEMENTATION-PLAN.md's
    "[the model pass] returns schema-valid JSON" requires enforcement of.
    """

    def __init__(self, message: str, *, raw_response: str) -> None:
        super().__init__(message)
        self.raw_response = raw_response


@dataclass(frozen=True, slots=True)
class ModelPassSpec:
    """Everything the caller supplies to run one constrained model pass.
    `command` is an arbitrary argv the caller controls (a wrapper script,
    a CLI, anything that reads a prompt on stdin and writes one JSON
    object to stdout); this module never talks to a specific provider."""

    command: tuple[str, ...]
    model: str
    temperature: float = 0.0
    timeout: float | None = 60.0
    env: Mapping[str, str] | None = None


def build_plan_envelope(
    plan: ProbePlan,
    *,
    pass_kind: Literal["deterministic", "model"],
    model: str | None = None,
    temperature: float | None = None,
    prompt_digest: str | None = None,
    response_digest: str | None = None,
) -> dict[str, Json]:
    """The `plan/v0` evidence envelope: `plan` plus model-pass provenance
    as sibling keys (`ProbePlan` itself never gains these fields --
    `extra="forbid"` keeps that interface untouched). All four provenance
    fields are explicit `null` for a pure deterministic-pass plan, not
    omitted, so "no model pass ran" is unambiguous on read. `temperature`
    is serialized as a string: `babi.canon.canonical_json` intentionally
    rejects non-integral floats, and this envelope is meant to survive
    `EvidenceStore.put_json` unmodified regardless of what temperature the
    caller used."""
    return {
        "kind": PLAN_KIND,
        "plan": plan.model_dump(mode="json"),
        "pass_kind": pass_kind,
        "model": model,
        "temperature": None if temperature is None else str(temperature),
        "prompt_digest": prompt_digest,
        "response_digest": response_digest,
    }


class ModelPlanner:
    def __init__(self, spec: ModelPassSpec, *, evidence: EvidenceStore | None = None) -> None:
        self.spec = spec
        self.evidence = evidence if evidence is not None else EvidenceStore()

    def build_prompt(
        self,
        *,
        server_subject: str,
        tool_catalog: Sequence[Mapping[str, Json]],
        deterministic_candidates: DeterministicCandidates,
        sandbox: SandboxManifest | None,
    ) -> str:
        """Deterministic prompt construction: only the tool catalog, the
        deterministic candidates, and sandbox constraints go in -- never a
        literal tool/operation name baked into the instruction text
        itself, only the caller-supplied catalog data."""
        context: dict[str, Json] = {
            "server_subject": server_subject,
            "tool_catalog": [dict(tool) for tool in tool_catalog],
            "deterministic_candidates": deterministic_candidates.to_json(),
            "sandbox_constraints": sandbox.model_dump(mode="json") if sandbox is not None else None,
            "response_json_schema": ProbePlan.model_json_schema(),
        }
        instructions = (
            "You are the constrained model pass of a behavioral-ABI probe planner.\n"
            "Input: a discovered MCP tool catalog, deterministic schema-derived candidate\n"
            "producer-consumer/read-back/inverse edges, and sandbox constraints.\n"
            "Task: fill semantically constrained argument values and rank the ambiguous\n"
            "dependency edges the deterministic pass already proposed.\n"
            "Coverage rule: include exactly one CALL_ONCE step for every tool in the\n"
            "catalog. Do not emit repeat, interrupt, concurrency, inverse, or mutation-\n"
            "fuzz steps unless the supplied sandbox constraints establish both a\n"
            "disposable scope and a working observer.\n"
            "Rules: never invent a tool, field, or dependency edge absent from the input\n"
            "below; never assert a dependency edge the deterministic pass did not already\n"
            "list as a candidate. Respond with exactly one JSON object that validates\n"
            "against `response_json_schema` -- no prose, no markdown fence, no trailing\n"
            "text before or after the object."
        )
        return f"{instructions}\n\n{json.dumps(context, indent=2, sort_keys=True)}\n"

    def run(
        self,
        *,
        server_subject: str,
        tool_catalog: Sequence[Mapping[str, Json]],
        deterministic_candidates: DeterministicCandidates,
        sandbox: SandboxManifest | None = None,
    ) -> ModelPassResult:
        prompt = self.build_prompt(
            server_subject=server_subject,
            tool_catalog=tool_catalog,
            deterministic_candidates=deterministic_candidates,
            sandbox=sandbox,
        )
        response_text = self._invoke(prompt)
        plan = self._validate(response_text)

        prompt_digest = self.evidence.put_bytes(prompt.encode("utf-8"))
        response_digest = self.evidence.put_bytes(response_text.encode("utf-8"))
        envelope = build_plan_envelope(
            plan,
            pass_kind="model",
            model=self.spec.model,
            temperature=self.spec.temperature,
            prompt_digest=prompt_digest,
            response_digest=response_digest,
        )
        envelope_digest = self.evidence.put_json(envelope)

        return ModelPassResult(
            plan=plan,
            envelope=envelope,
            envelope_digest=envelope_digest,
            prompt_digest=prompt_digest,
            response_digest=response_digest,
        )

    def _invoke(self, prompt: str) -> str:
        try:
            completed = subprocess.run(
                list(self.spec.command),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.spec.timeout,
                env=dict(self.spec.env) if self.spec.env is not None else None,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ModelInvocationError(f"model-pass command {self.spec.command!r} failed to run: {error}") from error
        if completed.returncode != 0:
            stderr_tail = completed.stderr.strip()[-2000:]
            raise ModelInvocationError(
                f"model-pass command {self.spec.command!r} exited {completed.returncode}: {stderr_tail}"
            )
        return completed.stdout

    def _validate(self, response_text: str) -> ProbePlan:
        stripped = response_text.strip()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as error:
            raise MalformedModelResponseError(
                f"model-pass response is not valid JSON: {error}", raw_response=response_text
            ) from error
        if not isinstance(parsed, dict):
            raise MalformedModelResponseError(
                f"model-pass response must be a JSON object, got {type(parsed).__name__}",
                raw_response=response_text,
            )
        try:
            plan = ProbePlan.model_validate(parsed)
        except ValidationError as error:
            raise MalformedModelResponseError(
                f"model-pass response does not validate as ProbePlan: {error}", raw_response=response_text
            ) from error

        step_ids = [step.id for step in plan.steps]
        if len(step_ids) != len(set(step_ids)):
            raise MalformedModelResponseError(
                "model-pass response contains duplicate step ids", raw_response=response_text
            )
        known_steps = set(step_ids)
        declared_edges = {
            (rationale.edge.from_step, rationale.edge.to_step)
            for rationale in plan.rationale_evidence
        }
        required_edges = {
            (dependency, step.id)
            for step in plan.steps
            for dependency in step.depends_on
        }
        unknown_dependencies = {
            dependency
            for dependency, _ in required_edges
            if dependency not in known_steps
        }
        missing_rationale = required_edges - declared_edges
        if unknown_dependencies or missing_rationale:
            raise MalformedModelResponseError(
                "model-pass response has invalid dependency provenance: "
                f"unknown_dependencies={sorted(unknown_dependencies)}, "
                f"missing_rationale={sorted(missing_rationale)}",
                raw_response=response_text,
            )
        return plan


@dataclass(frozen=True, slots=True)
class ModelPassResult:
    plan: ProbePlan
    envelope: dict[str, Json]
    envelope_digest: str
    prompt_digest: str
    response_digest: str


def _step_signature(step: ProbeStep) -> tuple[str, str]:
    return (step.tool_subject, step.primitive.value)


def _plan_step_multiset(plan: ProbePlan) -> Counter[tuple[str, str]]:
    return Counter(_step_signature(step) for step in plan.steps)


def _plan_edge_set(plan: ProbePlan) -> frozenset[tuple[tuple[str, str], tuple[str, str]]]:
    steps_by_id = {step.id: step for step in plan.steps}
    edges: set[tuple[tuple[str, str], tuple[str, str]]] = set()
    for step in plan.steps:
        to_signature = _step_signature(step)
        for dependency_id in step.depends_on:
            dependency_step = steps_by_id.get(dependency_id)
            if dependency_step is not None:
                edges.add((_step_signature(dependency_step), to_signature))
    return frozenset(edges)


def _multiset_jaccard(a: Counter[tuple[str, str]], b: Counter[tuple[str, str]]) -> float:
    if not a and not b:
        return 1.0
    intersection = sum((a & b).values())
    union = sum((a | b).values())
    return intersection / union if union else 1.0


def _set_jaccard(a: frozenset, b: frozenset) -> float:
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


class PlanAgreementReport(BaseModel):
    """Structural comparison of two independently-produced plans, at the
    level of `(tool_subject, primitive)` steps and the dependency edges
    induced between them -- never raw step ids, which two independent
    model calls have no reason to agree on. "Agreement is evidence about
    plan stability, not behavior truth" (IMPLEMENTATION-PLAN.md); nothing
    here is a behavioral verdict."""

    model_config = ConfigDict(extra="forbid")

    subject_digest_match: bool
    step_multiset_jaccard: float
    dependency_edge_jaccard: float
    cleanup_budget_match: bool
    observers_match: bool
    plan_a_step_count: int
    plan_b_step_count: int
    agreement_score: float


def compare_plans(plan_a: ProbePlan, plan_b: ProbePlan) -> PlanAgreementReport:
    step_jaccard = _multiset_jaccard(_plan_step_multiset(plan_a), _plan_step_multiset(plan_b))
    edge_jaccard = _set_jaccard(_plan_edge_set(plan_a), _plan_edge_set(plan_b))
    subject_match = plan_a.subject_digest == plan_b.subject_digest
    cleanup_match = plan_a.cleanup_budget == plan_b.cleanup_budget
    observers_match = set(plan_a.observers) == set(plan_b.observers)

    agreement_score = (
        (1.0 if subject_match else 0.0) + step_jaccard + edge_jaccard + (1.0 if cleanup_match else 0.0)
    ) / 4.0

    return PlanAgreementReport(
        subject_digest_match=subject_match,
        step_multiset_jaccard=step_jaccard,
        dependency_edge_jaccard=edge_jaccard,
        cleanup_budget_match=cleanup_match,
        observers_match=observers_match,
        plan_a_step_count=len(plan_a.steps),
        plan_b_step_count=len(plan_b.steps),
        agreement_score=agreement_score,
    )


__all__ = [
    "PLAN_KIND",
    "MalformedModelResponseError",
    "ModelInvocationError",
    "ModelPassResult",
    "ModelPassSpec",
    "ModelPlanner",
    "PlanAgreementReport",
    "build_plan_envelope",
    "compare_plans",
]
