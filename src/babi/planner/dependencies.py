"""Producer-consumer, read-back, and inverse candidate inference.

Every candidate here comes from exactly two generic signals, combined:

- **Schema overlap**: two fields (possibly on two different tools) share a
  structural `binding_key` (type + format + generic name-category).
- **Lexical token similarity**: the Jaccard similarity of the tokenized
  tool/field names and descriptions, with operation-verb tokens excluded
  (a shared "create"/"get" says nothing about which *resource* two tools
  share -- the noun tokens do; see `heuristics.token_jaccard`).

A candidate is always a scored, evidenced hypothesis -- IMPLEMENTATION-PLAN.md:
"[the model pass] ranks ambiguous dependency edges" and RUBRIC.md forbids
"[h]andwritten producer-consumer edges such as `create_issue -> get_issue`".
Nothing in this module ever asserts an edge is correct; `build.py` and the
model pass (`model.py`) are the only places a candidate can become part of
an actual `ProbePlan` dependency, and every resulting `RationaleEntry`
carries `RationaleProvenance.SCHEMA_OVERLAP` -- this module is the *only*
source of that provenance kind (the model pass may re-rank but never adds
a `SCHEMA_OVERLAP` edge this module did not first propose).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from babi.canon import Json
from babi.planner.heuristics import OperationClass, token_jaccard, tokenize
from babi.planner.schema import CatalogEntry, FieldSchema, SchemaAnalysis

# Score weights. Structural (`binding_key`) overlap is deliberately the
# *minority* contributor: the identifier binding key is coarse by design
# (type + format + generic name-category, e.g. `string:none:identifier`)
# -- effectively every opaque string id field in a whole catalog shares the
# same key, format-less ids being the overwhelmingly common case. Alone it
# is weak evidence; genuine confidence comes from *also* sharing lexical
# (resource-noun) similarity, which is why lexical similarity carries most
# of the weight below. A structural-only match (two unrelated resources
# that both happen to use a bare string id) lands near the low end of the
# range, at or below `build.py`'s candidate-acceptance floor; a match that
# is both structural and lexical lands near the top.
_TOOL_STRUCTURAL_WEIGHT = 0.25
_TOOL_LEXICAL_WEIGHT = 0.75
_FIELD_STRUCTURAL_WEIGHT = 0.35
_FIELD_LEXICAL_WEIGHT = 0.5
_TOOL_LEXICAL_BONUS_WEIGHT = 0.15


class CandidateKind(str, Enum):
    READ_BACK = "read-back"
    PRODUCER_CONSUMER = "producer-consumer"
    INVERSE = "inverse"


@dataclass(frozen=True, slots=True)
class DependencyCandidate:
    kind: CandidateKind
    from_tool_subject: str
    to_tool_subject: str
    from_field: tuple[str, ...] | None
    to_field: tuple[str, ...] | None
    score: float
    evidence: tuple[str, ...]

    def to_json(self) -> dict[str, Json]:
        return {
            "kind": self.kind.value,
            "from_tool_subject": self.from_tool_subject,
            "to_tool_subject": self.to_tool_subject,
            "from_field": list(self.from_field) if self.from_field is not None else None,
            "to_field": list(self.to_field) if self.to_field is not None else None,
            "score": self.score,
            "evidence": list(self.evidence),
        }


def _top_level_fields(analysis: SchemaAnalysis) -> tuple[FieldSchema, ...]:
    return analysis.root.properties if analysis.root is not None else ()


def _identifier_binding_keys(analysis: SchemaAnalysis) -> frozenset[str]:
    return frozenset(analysis.fields_by_path[path].binding_key for path in analysis.identifier_fields)


def _required_identifier_binding_keys(analysis: SchemaAnalysis) -> frozenset[str]:
    return frozenset(
        analysis.fields_by_path[path].binding_key
        for path in analysis.identifier_fields
        if analysis.fields_by_path[path].required
    )


def _sorted(candidates: list[DependencyCandidate]) -> tuple[DependencyCandidate, ...]:
    return tuple(sorted(candidates, key=lambda c: (-c.score, c.from_tool_subject, c.to_tool_subject, c.to_field or ())))


def infer_read_back_candidates(catalog: Sequence[CatalogEntry]) -> tuple[DependencyCandidate, ...]:
    """A state-changing tool (create/update/act) paired with a read-shaped
    sibling tool that plausibly observes its effect -- "candidate read-back
    tools" in IMPLEMENTATION-PLAN.md's deterministic-pass bullets."""
    candidates: list[DependencyCandidate] = []
    for producer in catalog:
        if producer.analysis.operation_class not in (OperationClass.CREATE, OperationClass.UPDATE, OperationClass.ACT):
            continue
        producer_keys = _identifier_binding_keys(producer.analysis.output) | _identifier_binding_keys(
            producer.analysis.input
        )
        for consumer in catalog:
            if consumer.tool_subject == producer.tool_subject or consumer.analysis.operation_class is not OperationClass.READ:
                continue
            consumer_input_keys = _required_identifier_binding_keys(consumer.analysis.input)
            consumer_output_keys = _identifier_binding_keys(consumer.analysis.output)
            input_overlap = producer_keys & consumer_input_keys
            output_overlap = producer_keys & consumer_output_keys
            structural_overlap = input_overlap | output_overlap
            lexical = token_jaccard(producer.analysis.tokens, consumer.analysis.tokens)
            if not structural_overlap and lexical <= 0.0:
                continue
            score = min(1.0, (_TOOL_STRUCTURAL_WEIGHT if structural_overlap else 0.0) + _TOOL_LEXICAL_WEIGHT * lexical)
            evidence: list[str] = []
            if input_overlap:
                evidence.append(f"schema-overlap: producer identifier(s) match read-back input {sorted(input_overlap)}")
            if output_overlap:
                evidence.append(f"schema-overlap: producer and read-back output identifiers match {sorted(output_overlap)}")
            if lexical > 0.0:
                evidence.append(f"lexical-token-similarity: {lexical:.2f}")
            candidates.append(
                DependencyCandidate(
                    kind=CandidateKind.READ_BACK,
                    from_tool_subject=producer.tool_subject,
                    to_tool_subject=consumer.tool_subject,
                    from_field=None,
                    to_field=None,
                    score=score,
                    evidence=tuple(evidence),
                )
            )
    return _sorted(candidates)


def infer_producer_consumer_candidates(catalog: Sequence[CatalogEntry]) -> tuple[DependencyCandidate, ...]:
    """Field-level producer -> consumer wiring: a required input field on
    one tool whose shape (structural, and/or lexical) overlaps an output
    field on another. For each `(producer, consumer, to_field)` the
    single best-scoring `from_field` is kept, to avoid combinatorial
    near-duplicate candidates for every unrelated field pair."""
    candidates: list[DependencyCandidate] = []
    for producer in catalog:
        producer_fields = _top_level_fields(producer.analysis.output)
        if not producer_fields:
            continue
        for consumer in catalog:
            if consumer.tool_subject == producer.tool_subject:
                continue
            tool_lexical = token_jaccard(producer.analysis.tokens, consumer.analysis.tokens)
            for to_field in _top_level_fields(consumer.analysis.input):
                if not to_field.required:
                    continue
                best: DependencyCandidate | None = None
                for from_field in producer_fields:
                    binding_match = from_field.binding_key == to_field.binding_key
                    field_lexical = token_jaccard(tokenize(from_field.name), tokenize(to_field.name))
                    if not binding_match and field_lexical <= 0.0:
                        continue
                    score = min(
                        1.0,
                        (_FIELD_STRUCTURAL_WEIGHT if binding_match else 0.0)
                        + _FIELD_LEXICAL_WEIGHT * field_lexical
                        + _TOOL_LEXICAL_BONUS_WEIGHT * tool_lexical,
                    )
                    evidence: list[str] = []
                    if binding_match:
                        evidence.append(
                            "schema-overlap: binding key "
                            f"{from_field.binding_key!r} shared by output.{'.'.join(from_field.path)} "
                            f"and input.{'.'.join(to_field.path)}"
                        )
                    if field_lexical > 0.0:
                        evidence.append(f"lexical-token-similarity(fields): {field_lexical:.2f}")
                    if tool_lexical > 0.0:
                        evidence.append(f"lexical-token-similarity(tools): {tool_lexical:.2f}")
                    candidate = DependencyCandidate(
                        kind=CandidateKind.PRODUCER_CONSUMER,
                        from_tool_subject=producer.tool_subject,
                        to_tool_subject=consumer.tool_subject,
                        from_field=from_field.path,
                        to_field=to_field.path,
                        score=score,
                        evidence=tuple(evidence),
                    )
                    if best is None or candidate.score > best.score:
                        best = candidate
                if best is not None:
                    candidates.append(best)
    return _sorted(candidates)


def infer_inverse_candidates(catalog: Sequence[CatalogEntry]) -> tuple[DependencyCandidate, ...]:
    """Candidate create <-> delete pairs -- IMPLEMENTATION-PLAN.md's
    "Candidate inverse tools" -- from opposite CRUD class plus schema/
    lexical overlap. `RUBRIC.md` forbids a *handwritten* inverse pair such
    as `create_customer -> delete_customer`; inferring the same edge from
    generic class + overlap signals is exactly what the deterministic
    pass is for."""
    candidates: list[DependencyCandidate] = []
    creators = [entry for entry in catalog if entry.analysis.operation_class is OperationClass.CREATE]
    deleters = [entry for entry in catalog if entry.analysis.operation_class is OperationClass.DELETE]
    for creator in creators:
        creator_keys = _identifier_binding_keys(creator.analysis.output) | _identifier_binding_keys(
            creator.analysis.input
        )
        for deleter in deleters:
            if deleter.tool_subject == creator.tool_subject:
                continue
            deleter_keys = _required_identifier_binding_keys(deleter.analysis.input)
            structural_overlap = creator_keys & deleter_keys
            lexical = token_jaccard(creator.analysis.tokens, deleter.analysis.tokens)
            if not structural_overlap and lexical <= 0.0:
                continue
            score = min(1.0, (_TOOL_STRUCTURAL_WEIGHT if structural_overlap else 0.0) + _TOOL_LEXICAL_WEIGHT * lexical)
            evidence = []
            if structural_overlap:
                evidence.append(f"schema-overlap: shared identifier binding key(s) {sorted(structural_overlap)}")
            if lexical > 0.0:
                evidence.append(f"lexical-token-similarity: {lexical:.2f}")
            candidates.append(
                DependencyCandidate(
                    kind=CandidateKind.INVERSE,
                    from_tool_subject=creator.tool_subject,
                    to_tool_subject=deleter.tool_subject,
                    from_field=None,
                    to_field=None,
                    score=score,
                    evidence=tuple(evidence),
                )
            )
    return _sorted(candidates)


@dataclass(frozen=True, slots=True)
class DeterministicCandidates:
    """Everything the deterministic pass concluded about a whole catalog --
    exactly what IMPLEMENTATION-PLAN.md says the model pass may receive:
    "the tool catalog, schemas, descriptions, sandbox constraints, and
    deterministic candidates.\""""

    catalog: tuple[CatalogEntry, ...]
    read_back: tuple[DependencyCandidate, ...]
    producer_consumer: tuple[DependencyCandidate, ...]
    inverse: tuple[DependencyCandidate, ...]

    def read_back_for(self, tool_subject: str, *, min_score: float = 0.0) -> tuple[DependencyCandidate, ...]:
        return tuple(c for c in self.read_back if c.from_tool_subject == tool_subject and c.score >= min_score)

    def inverse_for(self, tool_subject: str, *, min_score: float = 0.0) -> tuple[DependencyCandidate, ...]:
        return tuple(c for c in self.inverse if c.from_tool_subject == tool_subject and c.score >= min_score)

    def producer_consumer_for(self, tool_subject: str, *, min_score: float = 0.0) -> tuple[DependencyCandidate, ...]:
        return tuple(c for c in self.producer_consumer if c.to_tool_subject == tool_subject and c.score >= min_score)

    def to_json(self) -> dict[str, Json]:
        """A plain-JSON view for the model-pass prompt (`model.py`) --
        never round-tripped through `babi.canon` (candidate scores are
        fractional; `canon.canonical_json` intentionally rejects
        non-integral floats, so this is for prompt text only, not for
        content-addressed evidence)."""
        return {
            "catalog": [
                {
                    "tool_subject": entry.tool_subject,
                    "name": entry.analysis.name,
                    "operation_class": entry.analysis.operation_class.value,
                    "operation_confidence": entry.analysis.operation_confidence,
                    "required_input_fields": list(entry.analysis.input.required_fields),
                }
                for entry in self.catalog
            ],
            "read_back": [candidate.to_json() for candidate in self.read_back],
            "producer_consumer": [candidate.to_json() for candidate in self.producer_consumer],
            "inverse": [candidate.to_json() for candidate in self.inverse],
        }


def infer_all_candidates(catalog: Sequence[CatalogEntry]) -> DeterministicCandidates:
    entries = tuple(catalog)
    return DeterministicCandidates(
        catalog=entries,
        read_back=infer_read_back_candidates(entries),
        producer_consumer=infer_producer_consumer_candidates(entries),
        inverse=infer_inverse_candidates(entries),
    )


__all__ = [
    "CandidateKind",
    "DependencyCandidate",
    "DeterministicCandidates",
    "infer_all_candidates",
    "infer_inverse_candidates",
    "infer_producer_consumer_candidates",
    "infer_read_back_candidates",
]
