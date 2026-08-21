from babi.planner.heuristics import OperationClass, ValueCategory
from babi.planner.schema import analyze_catalog, analyze_schema, analyze_tool


def test_analyze_schema_none_is_empty_not_an_error() -> None:
    analysis = analyze_schema(None)
    assert analysis.root is None
    assert analysis.required_fields == ()
    assert analysis.identifier_fields == ()
    assert analysis.binding_keys == frozenset()


def test_analyze_schema_required_fields() -> None:
    analysis = analyze_schema(
        {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}, "c": {"type": "boolean"}},
            "required": ["a", "c"],
        }
    )
    assert analysis.required_fields == ("a", "c")
    assert analysis.fields_by_path[("a",)].required is True
    assert analysis.fields_by_path[("b",)].required is False


def test_analyze_schema_captures_const_default_enum_examples() -> None:
    analysis = analyze_schema(
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "fixed"},
                "level": {"type": "integer", "default": 3},
                "color": {"type": "string", "enum": ["red", "green"]},
                "note": {"type": "string", "examples": ["hello", "world"]},
            },
            "required": ["mode", "level", "color", "note"],
        }
    )
    fields = analysis.fields_by_path
    assert fields[("mode",)].has_const is True
    assert fields[("mode",)].const == "fixed"
    assert fields[("level",)].has_default is True
    assert fields[("level",)].default == 3
    assert fields[("color",)].enum == ("red", "green")
    assert fields[("note",)].examples == ("hello", "world")


def test_analyze_schema_const_none_is_distinguishable_from_absent() -> None:
    with_null_const = analyze_schema(
        {"type": "object", "properties": {"a": {"type": "string", "const": None}}, "required": ["a"]}
    )
    without_const = analyze_schema({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]})
    assert with_null_const.fields_by_path[("a",)].has_const is True
    assert with_null_const.fields_by_path[("a",)].const is None
    assert without_const.fields_by_path[("a",)].has_const is False


def test_analyze_schema_captures_bounds_and_format() -> None:
    analysis = analyze_schema(
        {
            "type": "object",
            "properties": {
                "age": {"type": "integer", "minimum": 0, "maximum": 130},
                "ratio": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
                "label": {"type": "string", "minLength": 2, "maxLength": 10, "pattern": "^[a-z]+$"},
                "id": {"type": "string", "format": "uuid"},
            },
            "required": ["age", "ratio", "label", "id"],
        }
    )
    fields = analysis.fields_by_path
    assert fields[("age",)].minimum == 0
    assert fields[("age",)].maximum == 130
    assert fields[("ratio",)].exclusive_minimum == 0
    assert fields[("ratio",)].exclusive_maximum == 1
    assert fields[("label",)].min_length == 2
    assert fields[("label",)].max_length == 10
    assert fields[("label",)].pattern == "^[a-z]+$"
    assert fields[("id",)].format == "uuid"


def test_analyze_schema_candidate_identifier_fields() -> None:
    analysis = analyze_schema(
        {
            "type": "object",
            "properties": {
                "widget_id": {"type": "string"},
                "session_token": {"type": "string"},
                "color": {"type": "string"},
            },
            "required": ["widget_id"],
        }
    )
    assert ("widget_id",) in analysis.identifier_fields
    assert ("session_token",) in analysis.identifier_fields
    assert ("color",) not in analysis.identifier_fields


def test_analyze_schema_recurses_into_nested_objects_and_arrays() -> None:
    analysis = analyze_schema(
        {
            "type": "object",
            "properties": {
                "address": {
                    "type": "object",
                    "properties": {"zip": {"type": "string"}},
                    "required": ["zip"],
                },
                "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
            },
            "required": ["address", "tags"],
        }
    )
    assert ("address", "zip") in analysis.fields_by_path
    assert analysis.fields_by_path[("address", "zip")].required is True
    assert analysis.fields_by_path[("tags",)].min_items == 1
    assert analysis.fields_by_path[("tags",)].max_items == 3
    assert ("tags", "[]") in analysis.fields_by_path
    assert analysis.fields_by_path[("tags", "[]")].json_types == ("string",)


def test_analyze_schema_implicit_object_type_from_properties() -> None:
    # No explicit "type": "object" -- properties alone should still be read
    # as an object, matching lax real-world schemas.
    analysis = analyze_schema({"properties": {"x": {"type": "string"}}, "required": ["x"]})
    assert analysis.root is not None
    assert analysis.root.json_types == ("object",)


def test_analyze_schema_resolves_defs_ref() -> None:
    schema = {
        "type": "object",
        "$defs": {"Widget": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
        "properties": {"widget": {"$ref": "#/$defs/Widget"}},
        "required": ["widget"],
    }
    analysis = analyze_schema(schema)
    assert ("widget", "id") in analysis.fields_by_path
    assert analysis.fields_by_path[("widget", "id")].required is True


def test_analyze_schema_recursive_ref_terminates() -> None:
    # A self-referential schema (a "tree") must not recurse forever.
    schema = {
        "type": "object",
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "children": {"type": "array", "items": {"$ref": "#/$defs/Node"}}},
                "required": ["label"],
            }
        },
        "properties": {"root": {"$ref": "#/$defs/Node"}},
        "required": ["root"],
    }
    analysis = analyze_schema(schema)  # must return, not hang or raise RecursionError
    assert analysis.root is not None


def test_analyze_schema_all_of_merges_branches() -> None:
    schema = {
        "allOf": [
            {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
            {"properties": {"b": {"type": "integer"}}, "required": ["b"]},
        ]
    }
    analysis = analyze_schema(schema)
    assert set(analysis.required_fields) == {"a", "b"}


def test_analyze_schema_any_of_takes_first_branch() -> None:
    schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    analysis = analyze_schema(schema)
    assert analysis.root is not None
    assert analysis.root.json_types == ("string",)


def test_analyze_tool_operation_class_from_verb() -> None:
    tool = {
        "name": "make_widget",
        "description": "Create a new widget.",
        "inputSchema": {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]},
    }
    analysis = analyze_tool(tool)
    assert analysis.operation_class is OperationClass.CREATE
    assert analysis.name == "make_widget"
    assert "widget" in analysis.tokens


def test_analyze_tool_operation_class_prefers_shape_when_verb_is_unknown() -> None:
    # No recognizable verb at all, but the input is *only* an identifier --
    # the shape signal should resolve this to READ rather than UNKNOWN.
    tool = {
        "name": "widgetinfo",
        "description": "Opaque widget accessor.",
        "inputSchema": {"type": "object", "properties": {"widget_id": {"type": "string"}}, "required": ["widget_id"]},
    }
    analysis = analyze_tool(tool)
    assert analysis.operation_class is OperationClass.READ


def test_analyze_tool_missing_output_schema_is_not_an_error() -> None:
    tool = {"name": "ping", "inputSchema": {"type": "object", "properties": {}}}
    analysis = analyze_tool(tool)
    assert analysis.output.root is None
    assert analysis.io_overlap == frozenset()


def test_analyze_tool_input_output_overlap() -> None:
    tool = {
        "name": "rename_widget",
        "description": "Rename an existing widget.",
        "inputSchema": {"type": "object", "properties": {"widget_id": {"type": "string"}}, "required": ["widget_id"]},
        "outputSchema": {"type": "object", "properties": {"widget_id": {"type": "string"}}, "required": ["widget_id"]},
    }
    analysis = analyze_tool(tool)
    assert "string:none:identifier" in analysis.io_overlap


def test_analyze_tool_annotations_are_captured_verbatim() -> None:
    tool = {
        "name": "make_widget",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    }
    analysis = analyze_tool(tool)
    assert analysis.annotations == {"readOnlyHint": False, "destructiveHint": True}


def test_analyze_catalog_preserves_order_and_wires_subjects() -> None:
    catalog = [
        {"name": "make_widget", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "fetch_widget", "inputSchema": {"type": "object", "properties": {}}},
    ]
    subjects = {"make_widget": "sha256:" + "a" * 64, "fetch_widget": "sha256:" + "b" * 64}
    entries = analyze_catalog(catalog, subjects)
    assert [entry.analysis.name for entry in entries] == ["make_widget", "fetch_widget"]
    assert entries[0].tool_subject == subjects["make_widget"]
    assert entries[1].tool_subject == subjects["fetch_widget"]


def test_analyze_catalog_missing_subject_is_empty_string_not_a_crash() -> None:
    catalog = [{"name": "unlisted_tool", "inputSchema": {"type": "object", "properties": {}}}]
    entries = analyze_catalog(catalog, {})
    assert entries[0].tool_subject == ""


def test_value_category_enum_membership_covers_flag_and_money() -> None:
    # Sanity check that ValueCategory covers every generic category the
    # heuristics table declares (schema.py must never invent a category
    # heuristics.py doesn't know about).
    assert ValueCategory.FLAG.value == "flag"
    assert ValueCategory.MONEY.value == "money"
