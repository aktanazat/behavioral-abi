import pytest

from babi.canon import Json
from babi.planner import synthesize_call
from babi.sandbox.synthesize import validates


@pytest.mark.parametrize(
    ("schema", "instance", "expected"),
    [
        (
            {
                "type": "object",
                "properties": {"n": {"type": "integer"}},
                "required": ["n"],
            },
            {"n": 1},
            True,
        ),
        (
            {
                "type": "object",
                "properties": {"n": {"type": "integer"}},
                "required": ["n"],
            },
            {},
            False,
        ),
        (
            {"type": "integer", "minimum": 5, "maximum": 10},
            11,
            False,
        ),
        (
            {"type": "string", "enum": ["red", "green"]},
            "red",
            True,
        ),
        (
            {"type": "array", "minItems": 2, "items": {"type": "string"}},
            ["a"],
            False,
        ),
        (
            {"const": "probe"},
            "other",
            False,
        ),
        (
            {"oneOf": [{"type": "string"}, {"type": "integer"}]},
            1,
            True,
        ),
    ],
)
def test_validates_supported_schema_subset(
    schema: dict[str, Json], instance: Json, expected: bool
) -> None:
    assert validates(schema, instance) is expected


def test_local_reference_resolution() -> None:
    schema = {
        "type": "object",
        "$defs": {"Id": {"type": "string", "minLength": 1}},
        "properties": {"id": {"$ref": "#/$defs/Id"}},
        "required": ["id"],
    }
    assert validates(schema, {"id": "widget-1"})
    assert not validates(schema, {"id": ""})


def test_planner_output_validates_end_to_end() -> None:
    tool = {
        "name": "mirror",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "times": {"type": "integer", "default": 1},
            },
            "required": ["text"],
        },
    }
    arguments = synthesize_call(tool)
    assert validates(tool["inputSchema"], arguments)
