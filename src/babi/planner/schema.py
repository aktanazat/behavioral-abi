"""Deterministic JSON Schema analysis.

Pure functions over one tool's wire definition (a `tools/list` entry: name,
description, `inputSchema`, optional `outputSchema`, `annotations`) or a
raw JSON Schema document. No network, no model call, no randomness --
IMPLEMENTATION-PLAN.md's "deterministic pass": required fields, types,
enum/const/default/examples, bounds, formats, candidate identifier fields,
candidate CRUD/act class, and input/output schema overlap.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from babi.canon import Json
from babi.planner.heuristics import (
    IDENTIFIER_VALUE_CATEGORIES,
    OperationClass,
    ValueCategory,
    classify_operation,
    classify_value_category,
    tokenize,
)

_MAX_SCHEMA_DEPTH = 8
_ABSENT = object()


@dataclass(frozen=True, slots=True)
class FieldSchema:
    """One property's fully-resolved constraint set: straight JSON Schema
    keyword extraction plus one generic name-pattern category lookup --
    nothing here is inferred beyond the schema's own declared shape and
    the field's own name."""

    name: str
    path: tuple[str, ...]
    json_types: tuple[str, ...]
    required: bool
    has_const: bool
    const: Json | None
    has_default: bool
    default: Json | None
    enum: tuple[Json, ...] | None
    examples: tuple[Json, ...]
    format: str | None
    pattern: str | None
    minimum: float | None
    maximum: float | None
    exclusive_minimum: float | None
    exclusive_maximum: float | None
    multiple_of: float | None
    min_length: int | None
    max_length: int | None
    min_items: int | None
    max_items: int | None
    items: "FieldSchema | None"
    properties: tuple["FieldSchema", ...]
    description: str | None
    value_category: ValueCategory
    binding_key: str

    @property
    def is_identifier_like(self) -> bool:
        return self.value_category in IDENTIFIER_VALUE_CATEGORIES

    @property
    def primary_type(self) -> str | None:
        return self.json_types[0] if self.json_types else None


def _normalize_types(raw_type: Json, *, has_properties: bool, has_items: bool) -> tuple[str, ...]:
    types: tuple[str, ...] = ()
    if isinstance(raw_type, str):
        types = (raw_type,)
    if isinstance(raw_type, list):
        types = tuple(item for item in raw_type if isinstance(item, str))
    if not types:
        if has_properties:
            types = ("object",)
        elif has_items:
            types = ("array",)
    return types


def _select_subschema(schema: dict) -> dict:
    """Collapse `allOf` (shallow-merged) and the first `oneOf`/`anyOf`
    branch into one schema dict. A deterministic, generic simplification
    of JSON Schema combinator semantics -- never keyed on a tool -- that
    covers the shapes Pydantic-model-generated tool schemas actually use.
    """
    if not isinstance(schema, dict):
        return {}
    merged = dict(schema)

    all_of = schema.get("allOf")
    if isinstance(all_of, list) and all_of:
        merged.pop("allOf", None)
        combined_properties: dict[str, Json] = dict(merged.get("properties") or {})
        combined_required: list[str] = list(merged.get("required") or [])
        for branch in all_of:
            if not isinstance(branch, dict):
                continue
            resolved = _select_subschema(branch)
            combined_properties.update(resolved.get("properties") or {})
            combined_required.extend(resolved.get("required") or [])
            for key, value in resolved.items():
                if key not in merged and key not in ("properties", "required"):
                    merged[key] = value
        if combined_properties:
            merged["properties"] = combined_properties
        if combined_required:
            merged["required"] = sorted(set(combined_required))

    for combinator in ("oneOf", "anyOf"):
        branches = schema.get(combinator)
        if isinstance(branches, list) and branches and isinstance(branches[0], dict):
            branch = dict(branches[0])
            for key, value in merged.items():
                if key not in branch and key != combinator:
                    branch[key] = value
            branch.pop(combinator, None)
            return _select_subschema(branch)

    return merged


def _resolve_ref(schema: dict, root: dict, *, depth: int) -> dict:
    ref = schema.get("$ref")
    if not isinstance(ref, str) or depth > _MAX_SCHEMA_DEPTH:
        return schema
    for prefix in ("#/$defs/", "#/definitions/"):
        if ref.startswith(prefix):
            def_name = ref[len(prefix) :]
            defs = root.get("$defs") if isinstance(root.get("$defs"), dict) else root.get("definitions")
            target = defs.get(def_name) if isinstance(defs, dict) else None
            if isinstance(target, dict):
                resolved = dict(target)
                for key, value in schema.items():
                    if key != "$ref":
                        resolved.setdefault(key, value)
                return _resolve_ref(resolved, root, depth=depth + 1)
    return schema  # remote/unresolvable ref: treated as opaque, falls through to untyped


def _field_schema(
    schema: Json, *, name: str, path: tuple[str, ...], required: bool, root: dict, depth: int
) -> FieldSchema:
    if not isinstance(schema, dict) or depth > _MAX_SCHEMA_DEPTH:
        schema_node: dict = {}
    else:
        schema_node = _select_subschema(_resolve_ref(schema, root, depth=depth))

    has_properties = isinstance(schema_node.get("properties"), dict)
    has_items = "items" in schema_node
    json_types = _normalize_types(schema_node.get("type"), has_properties=has_properties, has_items=has_items)

    examples_raw = schema_node.get("examples")
    examples = tuple(examples_raw) if isinstance(examples_raw, list) else ()

    enum_raw = schema_node.get("enum")
    enum = tuple(enum_raw) if isinstance(enum_raw, list) and enum_raw else None

    properties: tuple[FieldSchema, ...] = ()
    if "object" in json_types or has_properties:
        raw_properties = schema_node.get("properties")
        if isinstance(raw_properties, dict):
            required_children = set(schema_node.get("required") or [])
            properties = tuple(
                _field_schema(
                    child_schema,
                    name=child_name,
                    path=(*path, child_name),
                    required=child_name in required_children,
                    root=root,
                    depth=depth + 1,
                )
                for child_name, child_schema in raw_properties.items()
                if isinstance(child_name, str)
            )

    items_field: FieldSchema | None = None
    if "array" in json_types or has_items:
        raw_items = schema_node.get("items")
        item_schema = raw_items[0] if isinstance(raw_items, list) and raw_items else raw_items
        items_field = _field_schema(
            item_schema if isinstance(item_schema, dict) else {},
            name="item",
            path=(*path, "[]"),
            required=True,
            root=root,
            depth=depth + 1,
        )

    description = schema_node.get("description")
    description = description if isinstance(description, str) else None
    value_category = classify_value_category(name)
    primary_type = json_types[0] if json_types else ("object" if properties else "array" if items_field else "string")
    format_value = schema_node.get("format")
    format_value = format_value if isinstance(format_value, str) else None
    binding_key = f"{primary_type}:{format_value or 'none'}:{value_category.value}"

    def _num(key: str) -> float | None:
        value = schema_node.get(key)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def _int(key: str) -> int | None:
        value = schema_node.get(key)
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    const_value = schema_node.get("const", _ABSENT)
    default_value = schema_node.get("default", _ABSENT)
    pattern_value = schema_node.get("pattern")

    return FieldSchema(
        name=name,
        path=path,
        json_types=json_types,
        required=required,
        has_const=const_value is not _ABSENT,
        const=None if const_value is _ABSENT else const_value,
        has_default=default_value is not _ABSENT,
        default=None if default_value is _ABSENT else default_value,
        enum=enum,
        examples=examples,
        format=format_value,
        pattern=pattern_value if isinstance(pattern_value, str) else None,
        minimum=_num("minimum"),
        maximum=_num("maximum"),
        exclusive_minimum=_num("exclusiveMinimum"),
        exclusive_maximum=_num("exclusiveMaximum"),
        multiple_of=_num("multipleOf"),
        min_length=_int("minLength"),
        max_length=_int("maxLength"),
        min_items=_int("minItems"),
        max_items=_int("maxItems"),
        items=items_field,
        properties=properties,
        description=description,
        value_category=value_category,
        binding_key=binding_key,
    )


@dataclass(frozen=True, slots=True)
class SchemaAnalysis:
    root: FieldSchema | None
    required_fields: tuple[str, ...]
    fields_by_path: Mapping[tuple[str, ...], FieldSchema]
    identifier_fields: tuple[tuple[str, ...], ...]
    binding_keys: frozenset[str]


def _flatten(field_schema: FieldSchema, out: dict[tuple[str, ...], FieldSchema]) -> None:
    out[field_schema.path] = field_schema
    for child in field_schema.properties:
        _flatten(child, out)
    if field_schema.items is not None:
        _flatten(field_schema.items, out)


_EMPTY_SCHEMA_ANALYSIS = SchemaAnalysis(
    root=None, required_fields=(), fields_by_path={}, identifier_fields=(), binding_keys=frozenset()
)


def analyze_schema(schema: Mapping[str, Json] | None) -> SchemaAnalysis:
    """Analyze one JSON Schema document (a tool's `inputSchema` or
    `outputSchema`) in isolation. `schema=None` -- an MCP tool declaring no
    output schema is common -- yields the empty analysis, not an error:
    absence of a schema is data, not a defect here."""
    if not isinstance(schema, dict):
        return _EMPTY_SCHEMA_ANALYSIS

    root_field = _field_schema(schema, name="", path=(), required=True, root=schema, depth=0)
    fields_by_path: dict[tuple[str, ...], FieldSchema] = {}
    _flatten(root_field, fields_by_path)

    required_fields = tuple(child.name for child in root_field.properties if child.required)
    identifier_fields = tuple(f.path for f in fields_by_path.values() if f.path and f.is_identifier_like)
    binding_keys = frozenset(f.binding_key for f in fields_by_path.values() if f.path)

    return SchemaAnalysis(
        root=root_field,
        required_fields=required_fields,
        fields_by_path=fields_by_path,
        identifier_fields=identifier_fields,
        binding_keys=binding_keys,
    )


def _refine_operation_class(
    verb_class: OperationClass,
    verb_confidence: float,
    input_analysis: SchemaAnalysis,
    output_analysis: SchemaAnalysis,
) -> tuple[OperationClass, float]:
    """Blend the verb-token vote with one generic schema-shape signal: a
    tool whose every required input is identifier-shaped behaves like
    read/update/delete/act rather than create (a create call essentially
    never already knows the id of the thing it is about to create). This
    only resolves UNKNOWN/low-confidence votes; a confident verb vote is
    never overridden by shape alone."""
    root = input_analysis.root
    required_children = [child for child in (root.properties if root else ()) if child.required]
    only_identifier_input = bool(required_children) and all(child.is_identifier_like for child in required_children)
    produces_fresh_identifier = bool(output_analysis.identifier_fields) and not (
        output_analysis.binding_keys & input_analysis.binding_keys
    )

    if verb_class is not OperationClass.UNKNOWN and verb_confidence >= 0.6:
        return verb_class, verb_confidence

    if only_identifier_input and verb_class in (OperationClass.UNKNOWN, OperationClass.CREATE):
        resolved = OperationClass.READ if verb_class is OperationClass.UNKNOWN else verb_class
        return resolved, max(verb_confidence, 0.5)

    if produces_fresh_identifier and verb_class is OperationClass.UNKNOWN:
        return OperationClass.CREATE, 0.4

    return verb_class, verb_confidence


@dataclass(frozen=True, slots=True)
class ToolAnalysis:
    name: str
    description: str | None
    tokens: tuple[str, ...]
    operation_class: OperationClass
    operation_confidence: float
    input: SchemaAnalysis
    output: SchemaAnalysis
    annotations: Mapping[str, Json]
    io_overlap: frozenset[str]


def analyze_tool(tool_definition: Mapping[str, Json]) -> ToolAnalysis:
    """Deterministic per-tool analysis from one `tools/list` wire entry
    alone -- IMPLEMENTATION-PLAN.md's deterministic-pass bullets: required
    fields/types/enum/formats/bounds/defaults/examples, candidate
    identifier fields, candidate CRUD/act class, output/input overlap."""
    name = tool_definition.get("name")
    name = name if isinstance(name, str) else ""
    description = tool_definition.get("description")
    description = description if isinstance(description, str) else None

    verb_class, verb_confidence = classify_operation(name, description)

    input_schema = tool_definition.get("inputSchema")
    output_schema = tool_definition.get("outputSchema")
    input_analysis = analyze_schema(input_schema if isinstance(input_schema, dict) else None)
    output_analysis = analyze_schema(output_schema if isinstance(output_schema, dict) else None)

    operation_class, confidence = _refine_operation_class(verb_class, verb_confidence, input_analysis, output_analysis)

    annotations = tool_definition.get("annotations")
    annotations = dict(annotations) if isinstance(annotations, dict) else {}

    return ToolAnalysis(
        name=name,
        description=description,
        tokens=tokenize(name, description),
        operation_class=operation_class,
        operation_confidence=confidence,
        input=input_analysis,
        output=output_analysis,
        annotations=annotations,
        io_overlap=input_analysis.binding_keys & output_analysis.binding_keys,
    )


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    tool_subject: str
    definition: Mapping[str, Json]
    analysis: ToolAnalysis


def analyze_catalog(
    tool_catalog: Sequence[Mapping[str, Json]], tool_subjects: Mapping[str, str]
) -> tuple[CatalogEntry, ...]:
    """Analyze a whole discovered catalog at once -- `babi.collect`'s
    `tools_list` + `tool_subjects` mapping, verbatim -- the unit dependency
    inference (`dependencies.py`) and the plan builder (`build.py`)
    operate over. Order is preserved from the input catalog."""
    entries: list[CatalogEntry] = []
    for definition in tool_catalog:
        name = definition.get("name")
        name = name if isinstance(name, str) else ""
        entries.append(
            CatalogEntry(
                tool_subject=tool_subjects.get(name, ""),
                definition=dict(definition),
                analysis=analyze_tool(definition),
            )
        )
    return tuple(entries)


__all__ = [
    "CatalogEntry",
    "FieldSchema",
    "SchemaAnalysis",
    "ToolAnalysis",
    "analyze_catalog",
    "analyze_schema",
    "analyze_tool",
]
