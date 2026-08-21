"""Schema-driven argument binding, re-derived from live data at execution
time.

IMPLEMENTATION-PLAN.md authorizes "producer-consumer edges from output/input
schema overlap" as a deterministic-pass technique for the planner; this
module re-runs the same *kind* of shape-driven matching -- never a per-tool
or per-field-name special case -- against an actual prior response instead
of a static schema, to fill in a READ_BACK or VERIFY_WITH_TIMING step's call
arguments from the step it depends on. A field is bound only when its name
or shape gives real evidence of a match; anything unbindable is simply
omitted, never guessed.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

from babi.canon import Json

_ID_MARKER = "id"


def extract_structured_value(response: Json) -> Json:
    """The best-effort structured payload of a dumped `CallToolResult`
    (`{"content": [...], "structuredContent": ..., "isError": ...}`):
    `structuredContent` when present, else the first text content block
    parsed as JSON, else that block's raw text, else `None`.

    MCP tools frequently have no output schema at all (common today,
    especially pre-2025-11-25 servers), so the black-box observer chain
    must degrade to text prose rather than requiring structured output.
    """
    if not isinstance(response, dict):
        return None

    structured = response.get("structuredContent")
    if structured is not None:
        return structured

    content = response.get("content")
    if not isinstance(content, list):
        return None

    texts = [block["text"] for block in content if isinstance(block, dict) and block.get("type") == "text"]
    if not texts:
        return None
    try:
        return json.loads(texts[0])
    except (json.JSONDecodeError, TypeError):
        return "\n".join(texts)


def collection_of(value: Json) -> list[Json] | None:
    """`value` itself if it is a list, or the list inside the single-key
    `{"result": [...]}` wrapper MCP SDKs use when a tool's declared output
    schema requires a top-level object but the tool returns a bare list.
    `None` for anything else -- there is no collection to count here."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and set(value) == {"result"} and isinstance(value["result"], list):
        return value["result"]
    return None


def candidate_fields(value: Json) -> dict[str, Json]:
    """The name->value pool a binder can match schema properties against:
    every top-level key of a JSON object, verbatim. A list or scalar
    contributes no named fields -- there is nothing to bind a property name
    to without one."""
    return dict(value) if isinstance(value, dict) else {}


def build_source_pool(responses: Iterable[Json]) -> dict[str, Json]:
    """Merge `candidate_fields(extract_structured_value(r))` for every `r`
    in `responses` into one binding pool. Later entries win on key
    collision -- an arbitrary but deterministic tie-break for the rare case
    a step has more than one dependency contributing the same field name."""
    pool: dict[str, Json] = {}
    for response in responses:
        pool.update(candidate_fields(extract_structured_value(response)))
    return pool


def _schema_properties(input_schema: Mapping[str, Json]) -> list[str]:
    properties = input_schema.get("properties")
    if not isinstance(properties, dict):
        return []
    required = input_schema.get("required")
    required_set = set(required) if isinstance(required, list) else set()
    ordered = [name for name in properties if name in required_set]
    ordered += [name for name in properties if name not in required_set]
    return ordered


def _find_match(property_name: str, pool: Mapping[str, Json]) -> Json | None:
    if property_name in pool:
        return pool[property_name]

    lowered = property_name.lower()
    for key, value in pool.items():
        if key.lower() == lowered:
            return value

    if _ID_MARKER in lowered:
        id_like = [(key, value) for key, value in pool.items() if _ID_MARKER in key.lower()]
        if len(id_like) == 1:
            return id_like[0][1]

    return None


def bind_arguments(*, input_schema: Mapping[str, Json], source_pool: Mapping[str, Json]) -> dict[str, Json]:
    """Best-effort call arguments for a tool whose input schema is
    `input_schema`, drawn from `source_pool`. Matches, in order: an exact
    property-name key, a case-insensitive key, or -- when the property name
    itself looks identifier-shaped and exactly one pool field does too -- that
    unique identifier-shaped field. A schema with exactly one property and a
    pool with exactly one entry binds that pair even without a name match
    (the single-scalar fallback: nothing else could possibly be meant).
    Properties with no confident match are simply absent from the result.
    """
    properties = _schema_properties(input_schema)
    if len(properties) == 1 and len(source_pool) == 1:
        return {properties[0]: next(iter(source_pool.values()))}

    bound: dict[str, Json] = {}
    for name in properties:
        match = _find_match(name, source_pool)
        if match is not None:
            bound[name] = match
    return bound


__all__ = [
    "bind_arguments",
    "build_source_pool",
    "candidate_fields",
    "collection_of",
    "extract_structured_value",
]
