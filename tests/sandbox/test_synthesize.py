from babi.sandbox.synthesize import synthesize_arguments, validates


def test_required_only_by_default() -> None:
    schema = {
        "type": "object",
        "properties": {"value": {"type": "integer"}, "name": {"type": "string"}},
        "required": ["value"],
    }
    ok, args = synthesize_arguments(schema)
    assert ok
    assert args == {"value": 0}
    assert validates(schema, args)


def test_include_optional_fills_every_property() -> None:
    schema = {
        "type": "object",
        "properties": {"value": {"type": "integer"}, "name": {"type": "string"}},
        "required": ["value"],
    }
    ok, args = synthesize_arguments(schema, include_optional=True)
    assert ok
    assert set(args) == {"value", "name"}
    assert validates(schema, args)


def test_enum_uses_first_declared_value() -> None:
    schema = {"type": "object", "properties": {"color": {"type": "string", "enum": ["red", "green"]}}, "required": ["color"]}
    ok, args = synthesize_arguments(schema)
    assert ok and args == {"color": "red"}
    assert validates(schema, args)
    assert not validates(schema, {"color": "purple"})


def test_const_is_used_verbatim() -> None:
    schema = {"type": "object", "properties": {"kind": {"const": "probe"}}, "required": ["kind"]}
    ok, args = synthesize_arguments(schema)
    assert ok and args == {"kind": "probe"}
    assert validates(schema, args)


def test_bounded_integer_uses_the_minimum() -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer", "minimum": 5, "maximum": 10}}, "required": ["n"]}
    ok, args = synthesize_arguments(schema)
    assert ok and args == {"n": 5}
    assert validates(schema, args)
    assert not validates(schema, {"n": 999})


def test_exclusive_bounds_are_respected() -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer", "exclusiveMinimum": 5}}, "required": ["n"]}
    ok, args = synthesize_arguments(schema)
    assert ok and args == {"n": 6}
    assert validates(schema, args)


def test_contradictory_bounds_decline_rather_than_guess() -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer", "minimum": 10, "maximum": 5}}, "required": ["n"]}
    ok, args = synthesize_arguments(schema)
    assert not ok
    assert args == {}


def test_multiple_of_declines() -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer", "multipleOf": 7}}, "required": ["n"]}
    ok, _ = synthesize_arguments(schema)
    assert not ok


def test_regex_pattern_declines_rather_than_guess() -> None:
    schema = {"type": "object", "properties": {"s": {"type": "string", "pattern": "^[a-z]+$"}}, "required": ["s"]}
    ok, args = synthesize_arguments(schema)
    assert not ok
    assert args == {}


def test_string_format_uses_a_canned_sample() -> None:
    schema = {"type": "object", "properties": {"e": {"type": "string", "format": "email"}}, "required": ["e"]}
    ok, args = synthesize_arguments(schema)
    assert ok and "@" in args["e"]
    assert validates(schema, args)


def test_min_length_padding_still_validates() -> None:
    schema = {"type": "object", "properties": {"s": {"type": "string", "minLength": 8}}, "required": ["s"]}
    ok, args = synthesize_arguments(schema)
    assert ok and len(args["s"]) >= 8
    assert validates(schema, args)


def test_nested_object_and_array_with_min_items() -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 2,
                "items": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            }
        },
        "required": ["items"],
    }
    ok, args = synthesize_arguments(schema)
    assert ok
    assert args == {"items": [{"id": ""}, {"id": ""}]}
    assert validates(schema, args)


def test_array_without_items_schema_declines_when_nonempty_required() -> None:
    schema = {"type": "object", "properties": {"items": {"type": "array", "minItems": 1}}, "required": ["items"]}
    ok, _ = synthesize_arguments(schema)
    assert not ok


def test_empty_object_schema_synthesizes_empty_object() -> None:
    ok, args = synthesize_arguments({"type": "object"})
    assert ok and args == {}
    assert validates({"type": "object"}, args)


def test_missing_required_property_schema_declines() -> None:
    # `required` names a property that has no schema entry at all.
    schema = {"type": "object", "properties": {}, "required": ["ghost"]}
    ok, _ = synthesize_arguments(schema)
    assert not ok


def test_any_of_uses_first_satisfiable_branch() -> None:
    schema = {
        "type": "object",
        "properties": {"v": {"anyOf": [{"type": "string", "pattern": "^[a-z]+$"}, {"type": "integer"}]}},
        "required": ["v"],
    }
    ok, args = synthesize_arguments(schema)
    assert ok and args == {"v": 0}
    assert validates(schema, args)


def test_all_of_merges_object_branches() -> None:
    schema = {
        "type": "object",
        "properties": {
            "obj": {
                "allOf": [
                    {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]},
                    {"type": "object", "properties": {"b": {"type": "string"}}, "required": ["b"]},
                ]
            }
        },
        "required": ["obj"],
    }
    ok, args = synthesize_arguments(schema)
    assert ok
    assert args == {"obj": {"a": 0, "b": ""}}
    assert validates(schema, args)


def test_local_ref_resolution() -> None:
    schema = {
        "type": "object",
        "$defs": {"Id": {"type": "string", "minLength": 1}},
        "properties": {"id": {"$ref": "#/$defs/Id"}},
        "required": ["id"],
    }
    ok, args = synthesize_arguments(schema)
    assert ok and validates(schema, args)
    assert len(args["id"]) >= 1


def test_unresolvable_ref_declines() -> None:
    schema = {"type": "object", "properties": {"id": {"$ref": "#/$defs/DoesNotExist"}}, "required": ["id"]}
    ok, _ = synthesize_arguments(schema)
    assert not ok


def test_non_object_input_schema_declines() -> None:
    ok, args = synthesize_arguments("not-a-schema")  # type: ignore[arg-type]
    assert not ok
    assert args == {}


def test_validates_rejects_wrong_type() -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    assert not validates(schema, {"n": "not-an-integer"})


def test_validates_rejects_missing_required_property() -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    assert not validates(schema, {})


def test_fixture_server_schemas_are_realistic_and_pass_end_to_end() -> None:
    # Mirrors tests/fixtures/fake_server.py's actual tool schemas (the
    # shared fixture every other babi test round-trips against).
    ping_schema = {"type": "object", "properties": {"value": {"title": "Value", "type": "integer"}}, "required": ["value"]}
    ok, args = synthesize_arguments(ping_schema)
    assert ok and validates(ping_schema, args)

    mirror_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}, "times": {"type": "integer", "default": 1}},
        "required": ["text"],
    }
    ok, args = synthesize_arguments(mirror_schema)
    assert ok and args == {"text": ""} and validates(mirror_schema, args)
