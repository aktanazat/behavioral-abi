import json

from babi.canon import canonical_json
from babi.planner.schema import analyze_schema
from babi.planner.values import TypedBindings, synthesize_arguments, synthesize_call


def _args(schema: dict, **kwargs) -> dict:
    return synthesize_arguments(schema, **kwargs)


def test_same_schema_and_seed_is_byte_identical() -> None:
    schema = {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "count": {"type": "integer", "minimum": 1, "maximum": 100},
            "active": {"type": "boolean"},
            "tags": {"type": "array", "items": {"type": "string"}, "minItems": 2},
        },
        "required": ["label", "count", "active", "tags"],
    }
    first = _args(schema, seed="fixed-seed", resource_prefix="abi-probe")
    second = _args(schema, seed="fixed-seed", resource_prefix="abi-probe")
    assert first == second
    assert canonical_json(first) == canonical_json(second)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_different_seed_changes_output() -> None:
    schema = {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]}
    a = _args(schema, seed="seed-a")
    b = _args(schema, seed="seed-b")
    assert a != b


def test_different_resource_prefix_changes_output() -> None:
    schema = {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]}
    a = _args(schema, seed="same-seed", resource_prefix="prefix-a")
    b = _args(schema, seed="same-seed", resource_prefix="prefix-b")
    assert a != b


def test_const_always_wins() -> None:
    schema = {"type": "object", "properties": {"mode": {"type": "string", "const": "fixed"}}, "required": ["mode"]}
    for seed in ("s1", "s2", "s3"):
        assert _args(schema, seed=seed)["mode"] == "fixed"


def test_default_used_when_no_const() -> None:
    schema = {"type": "object", "properties": {"level": {"type": "integer", "default": 7}}, "required": ["level"]}
    assert _args(schema, seed="x")["level"] == 7


def test_enum_always_returns_a_member() -> None:
    schema = {
        "type": "object",
        "properties": {"color": {"type": "string", "enum": ["red", "green", "blue"]}},
        "required": ["color"],
    }
    for seed in ("s1", "s2", "s3", "s4", "s5"):
        assert _args(schema, seed=seed)["color"] in ("red", "green", "blue")


def test_examples_prefers_member_that_is_also_a_valid_enum_value() -> None:
    schema = {
        "type": "object",
        "properties": {
            "color": {
                "type": "string",
                "enum": ["red", "green"],
                "examples": ["blue", "green"],  # "blue" isn't legal; "green" is
            }
        },
        "required": ["color"],
    }
    for seed in ("s1", "s2", "s3", "s4", "s5"):
        assert _args(schema, seed=seed)["color"] == "green"


def test_required_only_by_default() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
        "required": ["a"],
    }
    result = _args(schema, seed="x")
    assert set(result) == {"a"}


def test_include_optional_fills_every_property() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
        "required": ["a"],
    }
    result = _args(schema, seed="x", include_optional=True)
    assert set(result) == {"a", "b"}


def test_missing_schema_yields_empty_arguments() -> None:
    assert _args(None, seed="x") == {}


def test_integer_respects_bounds() -> None:
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer", "minimum": 10, "maximum": 12}},
        "required": ["n"],
    }
    for seed in (str(i) for i in range(20)):
        value = _args(schema, seed=seed)["n"]
        assert isinstance(value, int) and not isinstance(value, bool)
        assert 10 <= value <= 12


def test_exclusive_bounds_are_respected() -> None:
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer", "exclusiveMinimum": 0, "exclusiveMaximum": 4}},
        "required": ["n"],
    }
    for seed in (str(i) for i in range(20)):
        value = _args(schema, seed=seed)["n"]
        assert 1 <= value <= 3


def test_number_respects_bounds() -> None:
    schema = {
        "type": "object",
        "properties": {"ratio": {"type": "number", "minimum": 0.0, "maximum": 1.0}},
        "required": ["ratio"],
    }
    for seed in (str(i) for i in range(20)):
        value = _args(schema, seed=seed)["ratio"]
        assert isinstance(value, float)
        assert 0.0 <= value <= 1.0


def test_multiple_of_is_respected() -> None:
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer", "minimum": 0, "maximum": 20, "multipleOf": 5}},
        "required": ["n"],
    }
    for seed in (str(i) for i in range(20)):
        value = _args(schema, seed=seed)["n"]
        assert value % 5 == 0
        assert 0 <= value <= 20


def test_boolean_type_produces_python_bool() -> None:
    schema = {"type": "object", "properties": {"flag": {"type": "boolean"}}, "required": ["flag"]}
    seen = {_args(schema, seed=str(i))["flag"] for i in range(20)}
    assert seen <= {True, False}
    for value in seen:
        assert isinstance(value, bool)


def test_array_uses_min_items_and_max_items() -> None:
    schema = {
        "type": "object",
        "properties": {"tags": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 5}},
        "required": ["tags"],
    }
    tags = _args(schema, seed="x")["tags"]
    assert 3 <= len(tags) <= 5
    assert all(isinstance(tag, str) for tag in tags)


def test_array_default_is_one_element_when_unconstrained() -> None:
    schema = {"type": "object", "properties": {"tags": {"type": "array", "items": {"type": "string"}}}, "required": ["tags"]}
    assert len(_args(schema, seed="x")["tags"]) == 1


def test_array_elements_vary_by_index() -> None:
    schema = {
        "type": "object",
        "properties": {"tags": {"type": "array", "items": {"type": "string"}, "minItems": 4}},
        "required": ["tags"],
    }
    tags = _args(schema, seed="x")["tags"]
    assert len(set(tags)) == len(tags), "distinct array positions must not collapse to the same synthesized value"


def test_object_recursion_fills_nested_required_fields() -> None:
    schema = {
        "type": "object",
        "properties": {
            "address": {
                "type": "object",
                "properties": {"zip": {"type": "string"}, "country": {"type": "string"}},
                "required": ["zip"],
            }
        },
        "required": ["address"],
    }
    address = _args(schema, seed="x")["address"]
    assert set(address) == {"zip"}
    assert isinstance(address["zip"], str)


def test_email_format_produces_at_sign() -> None:
    schema = {"type": "object", "properties": {"contact": {"type": "string", "format": "email"}}, "required": ["contact"]}
    value = _args(schema, seed="x")["contact"]
    assert "@" in value
    assert value.endswith("@example.test")


def test_uuid_format_is_rfc4122_v4_shaped() -> None:
    import uuid

    schema = {"type": "object", "properties": {"id": {"type": "string", "format": "uuid"}}, "required": ["id"]}
    value = _args(schema, seed="x")["id"]
    parsed = uuid.UUID(value)
    assert parsed.version == 4


def test_date_time_format_is_iso8601_z() -> None:
    schema = {"type": "object", "properties": {"at": {"type": "string", "format": "date-time"}}, "required": ["at"]}
    value = _args(schema, seed="x")["at"]
    assert value.endswith("Z")
    assert "T" in value


def test_path_name_category_looks_like_a_path() -> None:
    schema = {"type": "object", "properties": {"target_path": {"type": "string"}}, "required": ["target_path"]}
    value = _args(schema, seed="x")["target_path"]
    assert value.startswith("/")


def test_identifier_name_category_is_scoped_by_resource_prefix() -> None:
    schema = {"type": "object", "properties": {"widget_id": {"type": "string"}}, "required": ["widget_id"]}
    value = _args(schema, seed="x", resource_prefix="my-run-prefix")["widget_id"]
    assert value.startswith("my-run-prefix")


def test_min_length_and_max_length_are_respected_for_generated_strings() -> None:
    schema = {
        "type": "object",
        "properties": {"label": {"type": "string", "minLength": 20, "maxLength": 24}},
        "required": ["label"],
    }
    for seed in (str(i) for i in range(10)):
        value = _args(schema, seed=seed)["label"]
        assert 20 <= len(value) <= 24


def test_typed_bindings_reused_for_matching_binding_key() -> None:
    schema_analysis = analyze_schema(
        {"type": "object", "properties": {"widget_id": {"type": "string"}}, "required": ["widget_id"]}
    )
    binding_key = schema_analysis.fields_by_path[("widget_id",)].binding_key
    bindings = TypedBindings()
    bindings.record(binding_key, "already-observed-value")
    result = synthesize_arguments(schema_analysis, seed="x", bindings=bindings)
    assert result["widget_id"] == "already-observed-value"


def test_typed_bindings_lookup_returns_most_recent() -> None:
    bindings = TypedBindings()
    bindings.record("string:none:identifier", "first")
    bindings.record("string:none:identifier", "second")
    assert bindings.lookup("string:none:identifier") == "second"
    assert bindings.history("string:none:identifier") == ("first", "second")


def test_typed_bindings_lookup_missing_key_is_none() -> None:
    assert TypedBindings().lookup("string:none:identifier") is None


def test_synthesize_call_uses_tool_input_schema_only() -> None:
    tool = {
        "name": "make_widget",
        "inputSchema": {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]},
        "outputSchema": {"type": "object", "properties": {"widget_id": {"type": "string"}}, "required": ["widget_id"]},
    }
    result = synthesize_call(tool, seed="x")
    assert set(result) == {"label"}


def test_synthesize_call_default_seed_and_prefix_are_deterministic() -> None:
    tool = {"name": "ping", "inputSchema": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}}
    assert synthesize_call(tool) == synthesize_call(tool)
