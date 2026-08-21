"""RFC 8785 JCS vectors, including examples from the RFC itself."""

import json

import pytest

from babi import canon


def test_literals() -> None:
    assert canon.canonical_json(None) == b"null"
    assert canon.canonical_json(True) == b"true"
    assert canon.canonical_json(False) == b"false"


def test_no_whitespace() -> None:
    assert canon.canonical_json({"a": 1, "b": [1, 2]}) == b'{"a":1,"b":[1,2]}'


def test_object_keys_sorted_lexicographically() -> None:
    # RFC 8785 section 3.2.3, "in plain English" example.
    assert canon.canonical_json({"ab": 1, "a": 1, "aa": 1, "": 1}) == b'{"":1,"a":1,"aa":1,"ab":1}'


def test_array_element_order_preserved() -> None:
    assert canon.canonical_json([3, 1, 2]) == b"[3,1,2]"


def test_nested_objects_sorted_recursively() -> None:
    assert canon.canonical_json({"z": {"b": 1, "a": 1}, "a": 1}) == b'{"a":1,"z":{"a":1,"b":1}}'


def test_rfc8785_property_sort_vector() -> None:
    # RFC 8785 section 3.2.3: the six-property sorting test vector, verified
    # by checking value order after sorting (UTF-16 code unit order).
    obj = {
        "\u20ac": "Euro Sign",
        "\r": "Carriage Return",
        "\ufb33": "Hebrew Letter Dalet With Dagesh",
        "1": "One",
        "\U0001f600": "Emoji: Grinning Face",
        "\u0080": "Control",
        "\u00f6": "Latin Small Letter O With Diaeresis",
    }
    expected_order = [
        "Carriage Return",
        "One",
        "Control",
        "Latin Small Letter O With Diaeresis",
        "Euro Sign",
        "Emoji: Grinning Face",
        "Hebrew Letter Dalet With Dagesh",
    ]
    parsed = json.loads(canon.canonical_json(obj))
    assert list(parsed.values()) == expected_order


def test_string_escaping_predefined_controls() -> None:
    assert canon.canonical_json("\b\t\n\f\r") == b'"\\b\\t\\n\\f\\r"'


def test_string_escaping_other_control_chars_use_uhhhh() -> None:
    assert canon.canonical_json("\x01\x1f") == b'"\\u0001\\u001f"'


def test_string_escaping_quote_and_backslash() -> None:
    assert canon.canonical_json('"\\') == b'"\\"\\\\"'


def test_string_non_ascii_passed_through_as_utf8() -> None:
    # U+0080 is outside the ASCII control range (U+0000-U+001F) and MUST be
    # serialized as-is, not escaped, per RFC 8785 section 3.2.2.2.
    assert canon.canonical_json("\u0080\u00e9") == "\"\u0080\u00e9\"".encode()


def test_forward_slash_not_escaped() -> None:
    assert canon.canonical_json("/") == b'"/"'


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, b"0"),
        (-0.0, b"0"),  # RFC 8785 Appendix B: "Minus zero" -> 0
        (1, b"1"),
        (-1, b"-1"),
        (canon.MAX_SAFE_INTEGER, str(canon.MAX_SAFE_INTEGER).encode()),
        (-canon.MAX_SAFE_INTEGER, str(-canon.MAX_SAFE_INTEGER).encode()),
        (4.0, b"4"),  # integral float serializes without a decimal point
    ],
)
def test_number_serialization_vectors(value: int | float, expected: bytes) -> None:
    assert canon.canonical_json(value) == expected


def test_rejects_integers_outside_safe_range() -> None:
    # RFC 8785 Appendix B lists 9007199254740992 (2**53) as exactly
    # representable; canon.py deliberately narrows scope to the RFC's own
    # recommended safe-integer range rather than the full double range.
    with pytest.raises(ValueError, match="safe range"):
        canon.canonical_json(canon.MAX_SAFE_INTEGER + 1)


def test_rejects_non_integral_float_rather_than_guessing() -> None:
    with pytest.raises(ValueError, match="non-integral"):
        canon.canonical_json(333333333.3333333)


def test_rejects_nan_and_infinity() -> None:
    with pytest.raises(ValueError, match="NaN/Infinity"):
        canon.canonical_json(float("nan"))
    with pytest.raises(ValueError, match="NaN/Infinity"):
        canon.canonical_json(float("inf"))


def test_rejects_non_string_object_keys() -> None:
    with pytest.raises(TypeError, match="must be strings"):
        canon.canonical_json({1: "a"})  # type: ignore[dict-item]


def test_rejects_unsupported_types() -> None:
    with pytest.raises(TypeError, match="cannot serialize"):
        canon.canonical_json(object())


def test_digest_format_and_determinism() -> None:
    first = canon.digest({"b": 2, "a": 1})
    second = canon.digest({"a": 1, "b": 2})
    assert first == second
    assert first.startswith("sha256:")
    assert len(first) == len("sha256:") + 64


def test_digest_sensitive_to_content() -> None:
    assert canon.digest({"a": 1}) != canon.digest({"a": 2})


def test_bool_is_not_serialized_as_int() -> None:
    assert canon.canonical_json(True) != canon.canonical_json(1)
