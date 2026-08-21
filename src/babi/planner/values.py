"""Deterministic seeded value generation.

`generate_value` fills in one `FieldSchema` in a strict priority order:

1. `const` / `default` / `examples` / `enum` -- the target already published
   these; never invent when the schema (or the target) already told us.
2. An earlier value recorded in `TypedBindings` under this field's
   structural `binding_key` -- lets a plan reuse a value it already
   generated for a structurally-identical field elsewhere (the same
   schema-overlap signal `dependencies.py` uses to rank candidate edges,
   applied at the value level).
3. A generic JSON Schema `format` (uuid/date-time/email/uri/...).
4. Numeric bounds (`minimum`/`maximum`/`exclusiveMinimum`/
   `exclusiveMaximum`), for `integer`/`number`.
5. `boolean`.
6. `array`/`object`, recursively.
7. A safe opaque string scoped by `resource_prefix` -- the catch-all for an
   unconstrained string (or untyped) field, using one generic name-pattern
   category (`heuristics.classify_value_category`) to shape it a little
   (a `path`-shaped field looks like a path, an `email`-shaped field looks
   like an email) without ever depending on which *tool* the field belongs
   to.

Every synthesized value is a pure function of (schema shape, seed,
`context_path`, `resource_prefix`, prior bindings) -- no wall-clock time, no
OS randomness (`random.Random` is always constructed from a sha256 digest
of the seed material, never left to seed itself from OS entropy), no I/O.
The same inputs always reproduce the same output, byte for byte.
"""

from __future__ import annotations

import hashlib
import math
import random
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone

from babi.canon import Json
from babi.planner.heuristics import ValueCategory
from babi.planner.schema import FieldSchema, SchemaAnalysis, analyze_schema, analyze_tool

DEFAULT_RESOURCE_PREFIX = "abi-probe"
_MAX_RECURSION_DEPTH = 6
_SEED_DOMAIN = "babi.planner.values/v1"


class TypedBindings:
    """A generic store of concrete values observed or synthesized earlier,
    keyed only by `FieldSchema.binding_key` (type + format + name-category
    shape) -- never by a tool or field name literal. `lookup` returns the
    most recently recorded value for that shape, or `None`."""

    __slots__ = ("_values",)

    def __init__(self) -> None:
        self._values: dict[str, list[Json]] = {}

    def record(self, binding_key: str, value: Json) -> None:
        self._values.setdefault(binding_key, []).append(value)

    def lookup(self, binding_key: str) -> Json | None:
        values = self._values.get(binding_key)
        return values[-1] if values else None

    def history(self, binding_key: str) -> tuple[Json, ...]:
        return tuple(self._values.get(binding_key, ()))

    def __len__(self) -> int:
        return sum(len(values) for values in self._values.values())

    def __repr__(self) -> str:  # pragma: no cover -- debugging aid only
        return f"TypedBindings({self._values!r})"


def _seed_rng(seed: str, context_path: tuple[str, ...]) -> random.Random:
    material = "\x1f".join((_SEED_DOMAIN, seed, *context_path)).encode("utf-8")
    digest = hashlib.sha256(material).digest()
    return random.Random(digest)


def _slug(rng: random.Random, length: int = 8) -> str:
    return rng.randbytes((length // 2) + 1).hex()[:length]


def _fmt_uuid(rng: random.Random, _prefix: str) -> str:
    raw = bytearray(rng.randbytes(16))
    raw[6] = (raw[6] & 0x0F) | 0x40  # RFC 4122 version 4
    raw[8] = (raw[8] & 0x3F) | 0x80  # RFC 4122 variant 10
    return str(uuid.UUID(bytes=bytes(raw)))


def _fmt_datetime(rng: random.Random, _prefix: str) -> str:
    offset_seconds = rng.randint(0, 10_000_000)
    return datetime.fromtimestamp(offset_seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_date(rng: random.Random, prefix: str) -> str:
    return _fmt_datetime(rng, prefix)[:10]


def _fmt_time(rng: random.Random, prefix: str) -> str:
    return _fmt_datetime(rng, prefix)[11:19] + "Z"


def _fmt_email(rng: random.Random, prefix: str) -> str:
    return f"{prefix}-{_slug(rng)}@example.test"


def _fmt_uri(rng: random.Random, prefix: str) -> str:
    return f"https://example.test/{prefix}/{_slug(rng)}"


def _fmt_hostname(rng: random.Random, prefix: str) -> str:
    return f"{prefix}-{_slug(rng, 6)}.example.test"


def _fmt_ipv4(rng: random.Random, _prefix: str) -> str:
    # RFC 5737 TEST-NET-3: reserved for documentation, never routable.
    return f"203.0.113.{rng.randint(1, 254)}"


def _fmt_ipv6(rng: random.Random, _prefix: str) -> str:
    # RFC 3849 documentation prefix.
    return f"2001:db8::{rng.randint(1, 0xFFFF):x}"


_FORMAT_GENERATORS: dict[str, Callable[[random.Random, str], str]] = {
    "uuid": _fmt_uuid,
    "date-time": _fmt_datetime,
    "date": _fmt_date,
    "time": _fmt_time,
    "email": _fmt_email,
    "idn-email": _fmt_email,
    "uri": _fmt_uri,
    "iri": _fmt_uri,
    "url": _fmt_uri,
    "hostname": _fmt_hostname,
    "idn-hostname": _fmt_hostname,
    "ipv4": _fmt_ipv4,
    "ipv6": _fmt_ipv6,
}


def _cat_identifier(rng: random.Random, prefix: str) -> str:
    return f"{prefix}-{_slug(rng)}"


def _cat_path(rng: random.Random, prefix: str) -> str:
    return f"/tmp/{prefix}/{_slug(rng)}"


def _cat_name(rng: random.Random, prefix: str) -> str:
    return f"{prefix} {_slug(rng, 6)}"


def _cat_flag(rng: random.Random, _prefix: str) -> str:
    return "true" if rng.random() < 0.5 else "false"


def _cat_count(rng: random.Random, _prefix: str) -> str:
    return str(rng.randint(0, 10))


def _cat_money(rng: random.Random, _prefix: str) -> str:
    return f"{rng.uniform(0, 100):.2f}"


def _cat_generic(rng: random.Random, prefix: str) -> str:
    return f"{prefix}-{_slug(rng)}"


_CATEGORY_GENERATORS: dict[ValueCategory, Callable[[random.Random, str], str]] = {
    ValueCategory.IDENTIFIER: _cat_identifier,
    ValueCategory.PATH: _cat_path,
    ValueCategory.URL: _fmt_uri,
    ValueCategory.EMAIL: _fmt_email,
    ValueCategory.TIMESTAMP: _fmt_datetime,
    ValueCategory.NAME: _cat_name,
    ValueCategory.FLAG: _cat_flag,
    ValueCategory.COUNT: _cat_count,
    ValueCategory.MONEY: _cat_money,
    ValueCategory.GENERIC: _cat_generic,
}


def _clip_string(value: str, field: FieldSchema) -> str:
    if field.min_length is not None and len(value) < field.min_length:
        value = value + ("x" * (field.min_length - len(value)))
    if field.max_length is not None and len(value) > field.max_length:
        value = value[: field.max_length]
    return value


def _generate_string(rng: random.Random, field: FieldSchema, resource_prefix: str) -> str:
    if field.format:
        handler = _FORMAT_GENERATORS.get(field.format)
        if handler is not None:
            return _clip_string(handler(rng, resource_prefix), field)
    generator = _CATEGORY_GENERATORS.get(field.value_category, _cat_generic)
    return _clip_string(generator(rng, resource_prefix), field)


def _int_bounds(field: FieldSchema) -> tuple[int, int]:
    lo = math.ceil(field.minimum) if field.minimum is not None else 0
    if field.exclusive_minimum is not None:
        lo = max(lo, math.floor(field.exclusive_minimum) + 1)
    hi = math.floor(field.maximum) if field.maximum is not None else lo + 1000
    if field.exclusive_maximum is not None:
        hi = min(hi, math.ceil(field.exclusive_maximum) - 1)
    return lo, max(hi, lo)


def _generate_int(rng: random.Random, field: FieldSchema) -> int:
    lo, hi = _int_bounds(field)
    if field.multiple_of and field.multiple_of == int(field.multiple_of) and field.multiple_of > 0:
        step = int(field.multiple_of)
        k_min = -(-lo // step)  # ceil(lo / step)
        k_max = hi // step
        if k_max < k_min:
            k_max = k_min
        return rng.randint(k_min, k_max) * step
    return rng.randint(lo, hi)


def _number_bounds(field: FieldSchema) -> tuple[float, float]:
    if field.minimum is not None:
        lo = field.minimum
    elif field.exclusive_minimum is not None:
        lo = field.exclusive_minimum + 1e-6
    else:
        lo = 0.0
    if field.maximum is not None:
        hi = field.maximum
    elif field.exclusive_maximum is not None:
        hi = field.exclusive_maximum - 1e-6
    else:
        hi = lo + 1000.0
    return lo, max(hi, lo)


def _generate_number(rng: random.Random, field: FieldSchema) -> float:
    lo, hi = _number_bounds(field)
    return round(lo + rng.random() * (hi - lo), 6)


def _pick_examples_respecting_enum(field: FieldSchema, rng: random.Random) -> Json:
    candidates = list(field.examples)
    if field.enum is not None:
        enum_members = list(field.enum)
        safe = [example for example in candidates if example in enum_members]
        if safe:
            candidates = safe
        else:
            # No published example is actually a legal enum member (a
            # self-inconsistent schema); the hard constraint wins so the
            # synthesized call stays schema-valid.
            return rng.choice(enum_members)
    return rng.choice(candidates)


def generate_value(
    field: FieldSchema,
    *,
    seed: str,
    resource_prefix: str = DEFAULT_RESOURCE_PREFIX,
    bindings: TypedBindings | None = None,
    include_optional: bool = False,
    context_path: tuple[str, ...] = (),
    _depth: int = 0,
) -> Json:
    """Synthesize one JSON value for `field`. `context_path` is the
    *instance* path (varies per array index even though the underlying
    item schema is shared); `field.path` (used only for `binding_key`) is
    the *schema* path. Keeping these separate is what lets array elements
    differ from each other while staying fully deterministic."""
    rng = _seed_rng(seed, context_path)

    if field.has_const:
        return field.const
    if field.has_default:
        return field.default
    if field.examples:
        return _pick_examples_respecting_enum(field, rng)
    if field.enum:
        return rng.choice(list(field.enum))
    if bindings is not None:
        bound = bindings.lookup(field.binding_key)
        if bound is not None:
            return bound

    types = field.json_types
    if _depth >= _MAX_RECURSION_DEPTH:
        return None if types == ("null",) else f"{resource_prefix}-{_slug(rng)}"

    if "object" in types or (not types and field.properties):
        return _generate_object(
            field,
            seed=seed,
            resource_prefix=resource_prefix,
            bindings=bindings,
            include_optional=include_optional,
            context_path=context_path,
            depth=_depth,
        )
    if "array" in types or (not types and field.items is not None):
        return _generate_array(
            field,
            seed=seed,
            resource_prefix=resource_prefix,
            bindings=bindings,
            include_optional=include_optional,
            context_path=context_path,
            depth=_depth,
        )
    if "boolean" in types:
        return rng.random() < 0.5
    if "integer" in types:
        return _generate_int(rng, field)
    if "number" in types:
        return _generate_number(rng, field)
    if types == ("null",):
        return None

    # string, or no declared `type` at all.
    return _generate_string(rng, field, resource_prefix)


def _generate_object(
    field: FieldSchema,
    *,
    seed: str,
    resource_prefix: str,
    bindings: TypedBindings | None,
    include_optional: bool,
    context_path: tuple[str, ...],
    depth: int,
) -> dict[str, Json]:
    result: dict[str, Json] = {}
    for child in field.properties:
        if not child.required and not include_optional:
            continue
        result[child.name] = generate_value(
            child,
            seed=seed,
            resource_prefix=resource_prefix,
            bindings=bindings,
            include_optional=include_optional,
            context_path=(*context_path, child.name),
            _depth=depth + 1,
        )
    return result


def _generate_array(
    field: FieldSchema,
    *,
    seed: str,
    resource_prefix: str,
    bindings: TypedBindings | None,
    include_optional: bool,
    context_path: tuple[str, ...],
    depth: int,
) -> list[Json]:
    if field.items is None:
        return []
    count = field.min_items if field.min_items is not None else 1
    if field.max_items is not None:
        count = min(count, field.max_items)
    count = max(count, 0)
    return [
        generate_value(
            field.items,
            seed=seed,
            resource_prefix=resource_prefix,
            bindings=bindings,
            include_optional=include_optional,
            context_path=(*context_path, str(index)),
            _depth=depth + 1,
        )
        for index in range(count)
    ]


def synthesize_arguments(
    schema: Mapping[str, Json] | SchemaAnalysis | None,
    *,
    seed: str,
    resource_prefix: str = DEFAULT_RESOURCE_PREFIX,
    bindings: TypedBindings | None = None,
    include_optional: bool = False,
) -> dict[str, Json]:
    """Synthesize a schema-valid `arguments` object for one JSON Schema
    document (an `inputSchema`). Required properties only by default."""
    analysis = schema if isinstance(schema, SchemaAnalysis) else analyze_schema(schema)
    if analysis.root is None:
        return {}
    value = generate_value(
        analysis.root,
        seed=seed,
        resource_prefix=resource_prefix,
        bindings=bindings,
        include_optional=include_optional,
        context_path=(),
    )
    return value if isinstance(value, dict) else {}


def synthesize_call(
    tool_definition: Mapping[str, Json],
    *,
    resource_prefix: str = DEFAULT_RESOURCE_PREFIX,
    seed: str = "v0",
    bindings: TypedBindings | None = None,
    include_optional: bool = False,
) -> dict[str, Json]:
    """Deterministically synthesize a schema-valid `arguments` dict for one
    tool's `tools/call`, from its wire `inputSchema` alone. The same
    `(tool_definition, resource_prefix, seed)` always returns byte-identical
    JSON. Required fields only by default -- the minimal valid first call.
    """
    analysis = analyze_tool(tool_definition)
    return synthesize_arguments(
        analysis.input,
        seed=seed,
        resource_prefix=resource_prefix,
        bindings=bindings,
        include_optional=include_optional,
    )


__all__ = [
    "DEFAULT_RESOURCE_PREFIX",
    "TypedBindings",
    "generate_value",
    "synthesize_arguments",
    "synthesize_call",
]
