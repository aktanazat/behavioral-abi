"""Deterministic, schema-only first-call argument synthesis.

Fallback for `babi.planner.synthesize_call` (the shared generic planner's
constrained-value generator): if that module isn't available yet, the
corpus runner uses this one instead, with the same contract -- pure,
deterministic, and driven only by a tool's own JSON Schema (RUBRIC.md's
"Allowed": "JSON Schema constraints"), never by its name or description.

`synthesize_arguments` returns `ok=False` -- never a guessed value -- for
any construct it does not generically understand (a regex `pattern`, a
`multipleOf` bound, an unresolvable `$ref`, ...): declining is always
preferred to fabricating a plausible-looking but unjustified value.
`validates` independently re-derives whether a value satisfies a schema,
so a bug in the generator shows up as an `invalid` classification rather
than a silently wrong `success`.
"""

from __future__ import annotations


from babi.canon import Json

_FORMAT_SAMPLES: dict[str, str] = {
    "date-time": "1970-01-01T00:00:00Z",
    "date": "1970-01-01",
    "time": "00:00:00Z",
    "email": "probe@example.invalid",
    "hostname": "probe.example.invalid",
    "uri": "https://example.invalid/probe",
    "url": "https://example.invalid/probe",
    "uuid": "00000000-0000-0000-0000-000000000000",
    "ipv4": "192.0.2.1",
    "ipv6": "::1",
}

_JSON_TYPE_OF = {
    type(None): "null",
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    list: "array",
    dict: "object",
}


def _instance_types(instance: Json) -> set[str]:
    """Every JSON Schema `type` name `instance` satisfies -- a plain `int`
    also satisfies `"number"`, and `bool` is deliberately its own type
    (never `"integer"`), matching JSON Schema's own bool/number split."""
    if isinstance(instance, bool):
        return {"boolean"}
    if isinstance(instance, int):
        return {"integer", "number"}
    if isinstance(instance, float):
        return {"number"}
    if instance is None:
        return {"null"}
    if isinstance(instance, str):
        return {"string"}
    if isinstance(instance, list):
        return {"array"}
    if isinstance(instance, dict):
        return {"object"}
    return set()


def _resolve(schema: Json, root: dict[str, Json]) -> dict[str, Json] | None:
    """Follow one local `#/$defs/...` or `#/definitions/...` `$ref`.
    Anything else (external, non-JSON-pointer, or unresolvable) declines
    rather than guessing."""
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref")
    if ref is None:
        return schema
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    node: Json = root
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if not isinstance(node, dict):
        return None
    return node


def _declared_types(schema: dict[str, Json]) -> list[str] | None:
    declared = schema.get("type")
    if declared is None:
        return None
    if isinstance(declared, str):
        return [declared]
    if isinstance(declared, list) and all(isinstance(t, str) for t in declared):
        return list(declared)
    return None


def _synthesize_string(schema: dict) -> tuple[bool, Json]:
    if "pattern" in schema:
        return False, None
    fmt = schema.get("format")
    sample = _FORMAT_SAMPLES.get(fmt, "") if isinstance(fmt, str) else ""
    min_length = schema.get("minLength")
    if isinstance(min_length, int) and min_length > len(sample):
        sample = sample + "x" * (min_length - len(sample))
    max_length = schema.get("maxLength")
    if isinstance(max_length, int) and len(sample) > max_length:
        if isinstance(min_length, int) and min_length > max_length:
            return False, None
        sample = sample[:max_length]
    return True, sample


def _synthesize_number(schema: dict, *, integer: bool) -> tuple[bool, Json]:
    if "multipleOf" in schema:
        return False, None
    minimum = schema.get("minimum")
    exclusive_minimum = schema.get("exclusiveMinimum")
    maximum = schema.get("maximum")
    exclusive_maximum = schema.get("exclusiveMaximum")
    if not all(v is None or isinstance(v, (int, float)) for v in (minimum, exclusive_minimum, maximum, exclusive_maximum)):
        return False, None

    value: float = 0
    if isinstance(exclusive_minimum, (int, float)):
        value = max(value, exclusive_minimum + 1)
    if isinstance(minimum, (int, float)):
        value = max(value, minimum)
    if isinstance(exclusive_maximum, (int, float)) and value >= exclusive_maximum:
        value = exclusive_maximum - 1
    if isinstance(maximum, (int, float)) and value > maximum:
        value = maximum

    if isinstance(exclusive_minimum, (int, float)) and value <= exclusive_minimum:
        return False, None
    if isinstance(minimum, (int, float)) and value < minimum:
        return False, None
    if isinstance(exclusive_maximum, (int, float)) and value >= exclusive_maximum:
        return False, None
    if isinstance(maximum, (int, float)) and value > maximum:
        return False, None

    return True, int(value) if integer else float(value)


def _synthesize_array(schema: dict, root: dict, *, include_optional: bool) -> tuple[bool, Json]:
    min_items = schema.get("minItems")
    count = min_items if isinstance(min_items, int) and min_items > 0 else 0
    max_items = schema.get("maxItems")
    if isinstance(max_items, int) and count > max_items:
        return False, None

    if count == 0:
        return True, []

    item_schema = schema.get("items")
    if not isinstance(item_schema, dict):
        return False, None
    resolved_item = _resolve(item_schema, root)
    if resolved_item is None:
        return False, None
    ok, item_value = _synthesize(resolved_item, root, include_optional=include_optional)
    if not ok:
        return False, None
    return True, [item_value for _ in range(count)]


def _synthesize_object(schema: dict, root: dict, *, include_optional: bool) -> tuple[bool, Json]:
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    required = schema.get("required")
    required = set(required) if isinstance(required, list) else set()

    wanted = set(properties) if include_optional else required
    result: dict[str, Json] = {}
    for name in wanted:
        prop_schema = properties.get(name)
        if not isinstance(prop_schema, dict):
            if name in required:
                return False, None
            continue
        resolved_prop = _resolve(prop_schema, root)
        if resolved_prop is None:
            return False, None
        ok, value = _synthesize(resolved_prop, root, include_optional=include_optional)
        if not ok:
            if name in required:
                return False, None
            continue
        result[name] = value
    return True, result


def _synthesize(schema: dict, root: dict, *, include_optional: bool) -> tuple[bool, Json]:
    resolved = _resolve(schema, root)
    if resolved is None:
        return False, None
    schema = resolved

    if "const" in schema:
        return True, schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return True, enum[0]

    for combinator in ("anyOf", "oneOf"):
        branches = schema.get(combinator)
        if isinstance(branches, list) and branches:
            for branch in branches:
                if not isinstance(branch, dict):
                    continue
                resolved_branch = _resolve(branch, root)
                if resolved_branch is None:
                    continue
                ok, value = _synthesize(resolved_branch, root, include_optional=include_optional)
                if ok:
                    return True, value
            return False, None

    all_of = schema.get("allOf")
    if isinstance(all_of, list) and all_of:
        merged_properties: dict[str, Json] = {}
        merged_required: set[str] = set()
        for branch in all_of:
            if not isinstance(branch, dict):
                return False, None
            resolved_branch = _resolve(branch, root)
            if resolved_branch is None:
                return False, None
            branch_types = _declared_types(resolved_branch)
            if branch_types is not None and branch_types != ["object"]:
                return False, None
            branch_props = resolved_branch.get("properties")
            if isinstance(branch_props, dict):
                merged_properties.update(branch_props)
            branch_required = resolved_branch.get("required")
            if isinstance(branch_required, list):
                merged_required.update(branch_required)
        merged_schema = {"type": "object", "properties": merged_properties, "required": sorted(merged_required)}
        return _synthesize_object(merged_schema, root, include_optional=include_optional)

    types = _declared_types(schema)
    if types is None:
        return False, None

    for candidate in types:
        if candidate == "string":
            ok, value = _synthesize_string(schema)
        elif candidate == "integer":
            ok, value = _synthesize_number(schema, integer=True)
        elif candidate == "number":
            ok, value = _synthesize_number(schema, integer=False)
        elif candidate == "boolean":
            ok, value = True, False
        elif candidate == "null":
            ok, value = True, None
        elif candidate == "array":
            ok, value = _synthesize_array(schema, root, include_optional=include_optional)
        elif candidate == "object":
            ok, value = _synthesize_object(schema, root, include_optional=include_optional)
        else:
            ok, value = False, None
        if ok:
            return True, value
    return False, None


def synthesize_arguments(input_schema: dict, *, include_optional: bool = False) -> tuple[bool, dict[str, Json]]:
    """Deterministically synthesize a minimal (`include_optional=False`:
    required properties only) argument object structurally satisfying
    `input_schema`. Returns `(ok, arguments)`; `ok=False` means this
    generic generator declined -- never that it produced a wrong value."""
    if not isinstance(input_schema, dict):
        return False, {}
    ok, value = _synthesize(input_schema, input_schema, include_optional=include_optional)
    if not ok or not isinstance(value, dict):
        return False, {}
    return True, value


def _validates(schema: Json, root: dict[str, Json], instance: Json) -> bool:
    resolved = _resolve(schema, root)
    if resolved is None:
        return False
    schema = resolved

    if "const" in schema and instance != schema["const"]:
        return False
    enum = schema.get("enum")
    if isinstance(enum, list) and instance not in enum:
        return False

    for combinator, quantifier in (("anyOf", "any"), ("oneOf", "one")):
        branches = schema.get(combinator)
        if isinstance(branches, list) and branches:
            matches = sum(1 for branch in branches if isinstance(branch, dict) and _validates(branch, root, instance))
            if quantifier == "any" and matches < 1:
                return False
            if quantifier == "one" and matches != 1:
                return False

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        if not all(isinstance(branch, dict) and _validates(branch, root, instance) for branch in all_of):
            return False

    types = _declared_types(schema)
    if types is not None and not (_instance_types(instance) & set(types)):
        return False

    if isinstance(instance, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(instance) < min_length:
            return False
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(instance) > max_length:
            return False

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        for bound_key, op in (("minimum", "ge"), ("maximum", "le"), ("exclusiveMinimum", "gt"), ("exclusiveMaximum", "lt")):
            bound = schema.get(bound_key)
            if not isinstance(bound, (int, float)):
                continue
            if op == "ge" and instance < bound:
                return False
            if op == "le" and instance > bound:
                return False
            if op == "gt" and instance <= bound:
                return False
            if op == "lt" and instance >= bound:
                return False

    if isinstance(instance, list):
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(instance) < min_items:
            return False
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(instance) > max_items:
            return False
        item_schema = schema.get("items")
        if isinstance(item_schema, dict) and not all(_validates(item_schema, root, item) for item in instance):
            return False

    if isinstance(instance, dict):
        required = schema.get("required")
        if isinstance(required, list) and not all(name in instance for name in required):
            return False
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for name, value in instance.items():
                prop_schema = properties.get(name)
                if isinstance(prop_schema, dict) and not _validates(prop_schema, root, value):
                    return False

    return True


def validates(schema: dict, instance: Json) -> bool:
    """Independently re-derive whether `instance` satisfies `schema`,
    understanding exactly the constructs `synthesize_arguments` does --
    the corpus runner's own check that a synthesized call is actually
    valid, never trusted blindly from the generator that produced it."""
    return _validates(schema, schema, instance)


__all__ = ["synthesize_arguments", "validates"]
