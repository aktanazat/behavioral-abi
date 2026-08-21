"""Validate synthesized arguments against the supported JSON Schema subset."""

from __future__ import annotations

from babi.canon import Json


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


def validates(schema: dict[str, Json], instance: Json) -> bool:
    """Return whether `instance` satisfies the supported schema subset."""
    return _validates(schema, schema, instance)


__all__ = ["validates"]
