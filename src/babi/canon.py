"""RFC 8785 JSON Canonicalization Scheme (JCS): sorted keys, minimal escapes,
ECMAScript-compatible number serialization.

Scope: numbers are restricted to integers (or integral floats) within the
IEEE-754 safe-integer range [-(2**53-1), 2**53-1] -- the range RFC 8785
Appendix B Note (1) recommends for interoperable "true integers". Any other
float (non-integral, NaN/Infinity, or magnitude requiring the full
ECMAScript shortest-round-trip formatting algorithm) is rejected rather than
approximated.
"""

from __future__ import annotations

import hashlib
import math

MAX_SAFE_INTEGER = 2**53 - 1

type Json = None | bool | int | float | str | list[Json] | dict[str, Json]

_CONTROL_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
}


def _encode_string(value: str) -> str:
    parts = ['"']
    for ch in value:
        code = ord(ch)
        if ch == '"':
            parts.append('\\"')
        elif ch == "\\":
            parts.append("\\\\")
        elif code in _CONTROL_ESCAPES:
            parts.append(_CONTROL_ESCAPES[code])
        elif code < 0x20:
            parts.append(f"\\u{code:04x}")
        else:
            parts.append(ch)
    parts.append('"')
    return "".join(parts)


def _encode_number(value: int | float) -> str:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("canonical_json rejects NaN/Infinity")
        if not value.is_integer():
            raise ValueError(
                f"canonical_json only serializes integral numbers, got non-integral float {value!r}"
            )
        magnitude = int(value)
    else:
        magnitude = value

    if abs(magnitude) > MAX_SAFE_INTEGER:
        raise ValueError(
            f"canonical_json rejects integers outside the IEEE-754 safe range: {magnitude!r}"
        )
    return str(magnitude)


def _encode(value: Json) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _encode_number(value)
    if isinstance(value, str):
        return _encode_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise TypeError(f"canonical_json object keys must be strings, got {type(key)!r}")
        items = sorted(value.items(), key=lambda kv: kv[0].encode("utf-16-be"))
        body = ",".join(f"{_encode_string(key)}:{_encode(val)}" for key, val in items)
        return "{" + body + "}"
    raise TypeError(f"canonical_json cannot serialize {type(value)!r}")


def canonical_json(obj: Json) -> bytes:
    """Serialize `obj` to RFC 8785 JCS canonical JSON bytes."""
    return _encode(obj).encode("utf-8")


def digest(obj: Json) -> str:
    """sha256 digest of `obj`'s canonical JSON, as 'sha256:<hex>'."""
    return "sha256:" + hashlib.sha256(canonical_json(obj)).hexdigest()
