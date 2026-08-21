"""DSSE (Dead Simple Signing Envelope) v1.0.2: Pre-Authentication Encoding
(PAE) and the envelope wire shape.

Pure, dependency-free implementation of
https://github.com/secure-systems-lab/dsse -- no cryptography here, only
the byte-level encoding that gets signed and the JSON envelope shape that
carries the result. `babi.attest.cosign.CosignAttestor` is what actually
signs/verifies (by shelling out to `cosign`); this module only defines
what bytes mean what.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from babi.canon import Json

DSSE_PAYLOAD_TYPE_IN_TOTO = "application/vnd.in-toto+json"


class DsseError(ValueError):
    """Malformed DSSE input: not a signature-shaped dict, not valid base64,
    or missing a field the envelope spec requires."""


def pae(payload_type: str, body: bytes) -> bytes:
    """DSSEv1 Pre-Authentication Encoding -- what actually gets signed.

    ``PAE(type, body) = "DSSEv1" SP LEN(type) SP type SP LEN(body) SP body``

    SP is a single ASCII space; LEN(x) is x's UTF-8 byte length rendered as
    ASCII decimal digits with no leading zeros (which is exactly what
    Python's ``%d`` produces for a non-negative ``int``). Verified against
    the spec's own test vector in tests/attest/test_dsse.py.
    """
    type_bytes = payload_type.encode("utf-8")
    return b"DSSEv1 %d %b %d %b" % (len(type_bytes), type_bytes, len(body), body)


@dataclass(frozen=True, slots=True)
class Signature:
    """One entry of a DSSE envelope's `signatures` array. `keyid` is a hint
    only -- DSSE's own spec says it MUST NOT be used for security
    decisions, so it is never required."""

    sig: str  # base64-encoded signature bytes
    keyid: str | None = None

    def to_json(self) -> dict[str, Json]:
        obj: dict[str, Json] = {"sig": self.sig}
        if self.keyid is not None:
            obj["keyid"] = self.keyid
        return obj

    @staticmethod
    def from_json(obj: Json) -> "Signature":
        if not isinstance(obj, dict) or not isinstance(obj.get("sig"), str):
            raise DsseError(f"not a DSSE signature entry: {obj!r}")
        keyid = obj.get("keyid")
        return Signature(sig=obj["sig"], keyid=keyid if isinstance(keyid, str) else None)


@dataclass(frozen=True, slots=True)
class Envelope:
    """The DSSE JSON envelope: a payload, its type, and zero or more
    signatures over ``pae(payload_type, body)``. This class only carries
    and (de)serializes that shape; it never signs or verifies anything
    itself -- see ``babi.attest.cosign.CosignAttestor``.
    """

    payload_type: str
    body: bytes
    signatures: tuple[Signature, ...]

    def pae(self) -> bytes:
        """The exact bytes a signer/verifier must sign/check over."""
        return pae(self.payload_type, self.body)

    def to_json(self) -> dict[str, Json]:
        return {
            "payload": base64.standard_b64encode(self.body).decode("ascii"),
            "payloadType": self.payload_type,
            "signatures": [sig.to_json() for sig in self.signatures],
        }

    @staticmethod
    def from_json(obj: Json) -> "Envelope":
        if not isinstance(obj, dict):
            raise DsseError(f"not a DSSE envelope: {obj!r}")
        payload_b64 = obj.get("payload")
        payload_type = obj.get("payloadType")
        raw_signatures = obj.get("signatures")
        if not isinstance(payload_b64, str):
            raise DsseError("DSSE envelope missing string 'payload'")
        if not isinstance(payload_type, str):
            raise DsseError("DSSE envelope missing string 'payloadType'")
        if not isinstance(raw_signatures, list):
            raise DsseError("DSSE envelope missing list 'signatures'")
        try:
            body = base64.b64decode(payload_b64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise DsseError(f"DSSE envelope 'payload' is not valid base64: {error}") from error
        signatures = tuple(Signature.from_json(raw) for raw in raw_signatures)
        return Envelope(payload_type=payload_type, body=body, signatures=signatures)


__all__ = [
    "DSSE_PAYLOAD_TYPE_IN_TOTO",
    "DsseError",
    "Envelope",
    "Signature",
    "pae",
]
