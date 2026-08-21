"""Attestation: DSSE envelope construction and keyless Cosign/Fulcio/Rekor
signing for behavioral contracts. No custom cryptography -- all signature
math is delegated to the `cosign` binary."""

from __future__ import annotations

from babi.attest.cosign import CosignAttestor, CosignError, SigningResult, rekor_log_index_from_bundle
from babi.attest.dsse import DSSE_PAYLOAD_TYPE_IN_TOTO, DsseError, Envelope, Signature, pae

__all__ = [
    "DSSE_PAYLOAD_TYPE_IN_TOTO",
    "CosignAttestor",
    "CosignError",
    "DsseError",
    "Envelope",
    "Signature",
    "SigningResult",
    "pae",
    "rekor_log_index_from_bundle",
]
