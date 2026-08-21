"""Deterministic JSON canonicalization and content-addressed digests.

Canonical form is UTF-8 JSON with recursively sorted object keys, compact
separators, and no insignificant whitespace. Two values that are equal as
data always canonicalize to the same bytes regardless of dict insertion
order or source formatting, which is what makes digests over the result
reproducible across runs, processes, and tool-list page orderings.

This intentionally does not implement full RFC 8785 (JCS) numeric
normalization: every value babi hashes here is decoded from `json.loads` of
MCP wire data or built directly from plain Python literals, so there is no
path through which the same logical number reaches this function formatted
two different ways.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: Anything `json.dumps` can already serialize: the closed set of shapes
#: that appear in MCP tool/session metadata (schemas, annotations, plain
#: identifiers). No custom object ever reaches `canonical_json_bytes`.
CanonicalJson = dict[str, Any] | list[Any] | str | int | float | bool | None


def canonical_json_bytes(value: CanonicalJson) -> bytes:
    """Serialize `value` to a byte-stable canonical JSON encoding."""
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Return `data`'s digest as a lowercase `sha256:<hex>` string."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def digest_of(value: CanonicalJson) -> str:
    """Canonicalize `value` and return its content-addressed digest."""
    return sha256_hex(canonical_json_bytes(value))
