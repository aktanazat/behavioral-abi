"""Generic black-box read-back observer.

Consumes a planner-inferred dependency edge (the caller already resolved
*which* sibling tool verifies *which* prior step -- that is the plan's
`ProbeStep.tool_subject` + `depends_on`, decided by the planner) and derives
the actual call arguments itself, generically, via schema-overlap binding
against the dependency's own already-observed response
(`babi.execute.binding`). No tool name, field name, or per-operation
assumption is hardcoded here: the binder only matches by shape.

This is the one observer kind `babi.classify` treats as evidence about a
step's own *intended* effect -- everything else (local filesystem/process/
container/network observers, or a standalone `count_digest`) is ancillary
by construction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from mcp.client import Client

from babi.canon import Json
from babi.evidence import EvidenceStore
from babi.execute.binding import bind_arguments, collection_of, extract_structured_value
from babi.execute.wire import dump_model
from babi.history import ObservationStatus, ObserverResult
from babi.observe.count_digest import summarize

KIND = "read_back"

_SCALAR_TYPES = (str, int, float, bool)


@dataclass(frozen=True, slots=True)
class ReadBackOutcome:
    """`result` is the shared `ObserverResult`; `response` is the raw dumped
    `CallToolResult` (or `None` on failure), exposed so a caller chaining
    multiple read-backs (`VERIFY_WITH_TIMING`'s polling loop) can feed it
    back into `build_source_pool` for the next attempt."""

    result: ObserverResult
    response: dict[str, Json] | None


def _present(structured: Json, bound_arguments: Mapping[str, Json]) -> bool:
    """Whether `structured` plausibly confirms the resource identified by
    `bound_arguments` exists: if we bound no identifier-shaped value, a
    successful non-error response is itself the observation (e.g. a
    listing tool needs no identifier to be "present"). Otherwise, look for
    any bound scalar value appearing in the response -- shallow, generic,
    never keyed on a specific field name.
    """
    identifier_values = {value for value in bound_arguments.values() if isinstance(value, _SCALAR_TYPES)}
    if not identifier_values:
        return True

    collection = collection_of(structured)
    if collection is not None:
        return any(_contains_any(item, identifier_values) for item in collection)
    if isinstance(structured, dict):
        return _contains_any(structured, identifier_values)
    return True


def _contains_any(item: Json, values: set[Json]) -> bool:
    if isinstance(item, dict):
        return any(value in values for value in item.values() if isinstance(value, _SCALAR_TYPES))
    return item in values


async def read_back(
    client: Client,
    *,
    tool_name: str,
    input_schema: Mapping[str, Json],
    source_pool: Mapping[str, Json],
    literal_arguments: Mapping[str, Json] | None,
    evidence: EvidenceStore,
) -> ReadBackOutcome:
    """Call `tool_name` with arguments bound from `source_pool` (literal
    overrides win on key collision), and classify whether the result
    confirms the bound resource. Any exception -- the read-back call
    itself failing -- degrades to `inconclusive`, never a guessed verdict.
    """
    arguments = {**bind_arguments(input_schema=input_schema, source_pool=source_pool), **(literal_arguments or {})}

    try:
        call_result = await client.call_tool(tool_name, arguments)
    except Exception as exc:  # noqa: BLE001 -- a failed read-back is inconclusive evidence, not a crash
        result = ObserverResult(
            kind=KIND,
            status=ObservationStatus.inconclusive,
            value={"arguments": arguments},
            evidence_digest=None,
            detail=f"read-back call to {tool_name!r} failed: {exc}",
        )
        return ReadBackOutcome(result=result, response=None)

    dumped = dump_model(call_result)
    evidence_digest = evidence.put_json(dumped)
    structured = extract_structured_value(dumped)
    summary = summarize(structured)

    is_error = bool(dumped.get("isError"))
    found = False if is_error else _present(structured, arguments)

    result = ObserverResult(
        kind=KIND,
        status=ObservationStatus.observed_true if found else ObservationStatus.observed_false,
        value={"count": summary.count, "digest": summary.digest, "arguments": arguments},
        evidence_digest=evidence_digest,
        detail=None,
    )
    return ReadBackOutcome(result=result, response=dumped)


__all__ = ["KIND", "ReadBackOutcome", "read_back"]
