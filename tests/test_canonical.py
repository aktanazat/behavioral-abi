"""babi.canonical: key-order/formatting invariance and digest stability."""

import hashlib

from babi.canonical import canonical_json_bytes, digest_of, sha256_hex


def test_canonical_json_bytes_ignores_key_order():
    a = {"b": 1, "a": 2, "c": {"y": 1, "x": 2}}
    b = {"a": 2, "c": {"x": 2, "y": 1}, "b": 1}
    assert canonical_json_bytes(a) == canonical_json_bytes(b)


def test_canonical_json_bytes_is_compact_with_no_whitespace():
    encoded = canonical_json_bytes({"a": 1, "b": [1, 2, 3]})
    assert encoded == b'{"a":1,"b":[1,2,3]}'


def test_canonical_json_bytes_preserves_list_order():
    encoded = canonical_json_bytes({"items": [3, 1, 2]})
    assert encoded == b'{"items":[3,1,2]}'


def test_digest_of_matches_sha256_of_canonical_bytes():
    value = {"x": 1, "y": [1, 2]}
    expected = "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    assert digest_of(value) == expected


def test_digest_of_differs_for_different_content():
    assert digest_of({"a": 1}) != digest_of({"a": 2})


def test_digest_of_stable_regardless_of_key_order():
    assert digest_of({"a": 1, "b": 2}) == digest_of({"b": 2, "a": 1})


def test_sha256_hex_has_stable_prefix_and_length():
    digest = sha256_hex(b"")
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64
