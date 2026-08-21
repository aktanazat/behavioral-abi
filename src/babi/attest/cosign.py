"""CosignAttestor: keyless-by-default Cosign/Fulcio/Rekor-v2 signing and
verification for DSSE-wrapped behavioral contracts.

Every signature is produced and checked by shelling out to the `cosign`
binary -- this module never implements signature math itself (RUBRIC.md
"Do not build ... custom cryptography"). It signs the DSSE
Pre-Authentication Encoding (PAE) of the in-toto Statement, not the
Statement's bare bytes, so the resulting signature is genuine DSSE
(``SIGNATURE = Sign(PAE(payloadType, body))``) even though the underlying
tool is a general-purpose blob signer.

Cosign is invoked with argv lists only (never a shell string), and with
``--bundle`` so the signature, certificate, and Rekor inclusion proof all
land in one verifiable file. Verified empirically against cosign v3.1.3
(2026-08-20, local `--key` signing against the public Rekor v2 instance):
with ``--bundle``, `sign-blob` writes the base64 signature to
``bundle["messageSignature"]["signature"]`` -- *not* stdout, unlike its
own `--help` text suggests for the no-bundle path -- and a Rekor v2 log
entry to ``bundle["verificationMaterial"]["tlogEntries"][0]["logIndex"]``.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from babi.attest.dsse import DSSE_PAYLOAD_TYPE_IN_TOTO, Envelope, Signature, pae
from babi.canon import Json, canonical_json


class CosignError(RuntimeError):
    """A `cosign` invocation could not be run, failed, or its output could
    not be parsed."""


def rekor_log_index_from_bundle(bundle: Json) -> int | None:
    """Best-effort Rekor v2 log index from a cosign `--bundle` document.
    `None` (never fabricated, never raised) if the bundle wasn't logged to
    Rekor or doesn't have the shape this was written against."""
    if not isinstance(bundle, dict):
        return None
    verification_material = bundle.get("verificationMaterial")
    if not isinstance(verification_material, dict):
        return None
    entries = verification_material.get("tlogEntries")
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
        return None
    try:
        return int(entries[0].get("logIndex"))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class SigningResult:
    """One completed `cosign sign-blob` run: the DSSE envelope (payload +
    the signature cosign produced) and the raw Sigstore bundle it wrote
    (certificate, Rekor inclusion proof, everything `verify-blob` needs)."""

    envelope: Envelope
    bundle: dict[str, Json]

    @property
    def rekor_log_index(self) -> int | None:
        return rekor_log_index_from_bundle(self.bundle)


class CosignAttestor:
    """Shells out to `cosign sign-blob`/`cosign verify-blob` for keyless
    Fulcio/Rekor-v2 DSSE signing -- omitting `--key` (the default) is what
    triggers cosign's keyless OIDC flow; passing `key_path` switches to
    local-keypair signing (useful for tests and offline development, still
    logged to Rekor unless the caller's own cosign config disables it).
    """

    def __init__(
        self,
        *,
        certificate_identity_regexp: str,
        certificate_oidc_issuer_regexp: str,
        cosign_path: str = "cosign",
        payload_type: str = DSSE_PAYLOAD_TYPE_IN_TOTO,
        key_path: str | None = None,
        extra_sign_args: tuple[str, ...] = (),
        extra_verify_args: tuple[str, ...] = (),
        timeout: float = 120.0,
    ) -> None:
        if not certificate_identity_regexp or not certificate_oidc_issuer_regexp:
            raise ValueError(
                "CosignAttestor requires non-empty certificate_identity_regexp and "
                "certificate_oidc_issuer_regexp -- there is no safe default identity to verify "
                "against; RUBRIC.md forbids the founder standing in as the root of trust"
            )
        self._cosign_path = cosign_path
        self._payload_type = payload_type
        self._certificate_identity_regexp = certificate_identity_regexp
        self._certificate_oidc_issuer_regexp = certificate_oidc_issuer_regexp
        self._key_path = key_path
        self._extra_sign_args = tuple(extra_sign_args)
        self._extra_verify_args = tuple(extra_verify_args)
        self._timeout = timeout

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(  # noqa: S603 -- argv is a fixed list, never shell=True, never string-interpolated
                argv,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except OSError as error:
            raise CosignError(f"could not execute {argv[0]!r}: {error}") from error
        except subprocess.TimeoutExpired as error:
            raise CosignError(f"{argv[0]!r} timed out after {self._timeout}s") from error

    def _pae_bytes(self, statement: dict[str, Json]) -> bytes:
        return pae(self._payload_type, canonical_json(statement))

    def sign(self, statement: dict[str, Json]) -> SigningResult:
        """Sign `statement`'s DSSE PAE bytes via `cosign sign-blob --yes
        --bundle <bundle> <statement>`, keyless by default."""
        pae_bytes = self._pae_bytes(statement)

        with tempfile.TemporaryDirectory(prefix="babi-cosign-sign-") as tmp:
            statement_path = Path(tmp) / "statement.dsse-pae"
            bundle_path = Path(tmp) / "bundle.sigstore.json"
            statement_path.write_bytes(pae_bytes)

            argv = [self._cosign_path, "sign-blob", "--yes"]
            if self._key_path is not None:
                argv += ["--key", self._key_path]
            argv += ["--bundle", str(bundle_path), *self._extra_sign_args, str(statement_path)]

            result = self._run(argv)
            if result.returncode != 0:
                raise CosignError(f"cosign sign-blob failed (exit {result.returncode}): {result.stderr.strip()}")

            try:
                bundle = json.loads(bundle_path.read_text())
            except FileNotFoundError as error:
                raise CosignError("cosign sign-blob exited 0 but wrote no bundle file") from error
            except json.JSONDecodeError as error:
                raise CosignError(f"cosign sign-blob wrote an unparseable bundle: {error}") from error

        signature_b64 = _extract_signature(bundle, stdout=result.stdout)
        if not signature_b64:
            raise CosignError("cosign sign-blob produced no signature (checked bundle and stdout)")

        envelope = Envelope(
            payload_type=self._payload_type,
            body=canonical_json(statement),
            signatures=(Signature(sig=signature_b64, keyid=None),),
        )
        return SigningResult(envelope=envelope, bundle=bundle)

    def verify(self, statement: dict[str, Json], bundle: dict[str, Json]) -> bool:
        """Verify `bundle` against `statement`'s DSSE PAE bytes via
        `cosign verify-blob --bundle <bundle> --certificate-identity-regexp
        ... --certificate-oidc-issuer-regexp ... <statement>`. Returns
        False (never raises) on a verification failure; raises only if
        `cosign` itself could not be run."""
        pae_bytes = self._pae_bytes(statement)

        with tempfile.TemporaryDirectory(prefix="babi-cosign-verify-") as tmp:
            statement_path = Path(tmp) / "statement.dsse-pae"
            bundle_path = Path(tmp) / "bundle.sigstore.json"
            statement_path.write_bytes(pae_bytes)
            bundle_path.write_text(json.dumps(bundle))

            argv = [self._cosign_path, "verify-blob", "--bundle", str(bundle_path)]
            if self._key_path is not None:
                argv += ["--key", _public_key_path(self._key_path)]
            else:
                argv += [
                    "--certificate-identity-regexp",
                    self._certificate_identity_regexp,
                    "--certificate-oidc-issuer-regexp",
                    self._certificate_oidc_issuer_regexp,
                ]
            argv += [*self._extra_verify_args, str(statement_path)]

            result = self._run(argv)
            return result.returncode == 0


def _extract_signature(bundle: Json, *, stdout: str) -> str | None:
    """The base64 signature: `--bundle` mode puts it in
    `messageSignature.signature`; fall back to stdout in case a future
    cosign version (or `--signing-config`) prints it there too."""
    if isinstance(bundle, dict):
        message_signature = bundle.get("messageSignature")
        if isinstance(message_signature, dict):
            signature = message_signature.get("signature")
            if isinstance(signature, str) and signature:
                return signature
    stripped = stdout.strip()
    return stripped or None


def _public_key_path(private_key_path: str) -> str:
    """cosign's convention: `foo.key` signs, `foo.pub` verifies."""
    path = Path(private_key_path)
    if path.suffix == ".key":
        return str(path.with_suffix(".pub"))
    return f"{private_key_path}.pub"


__all__ = [
    "CosignAttestor",
    "CosignError",
    "SigningResult",
    "rekor_log_index_from_bundle",
]
