"""CosignAttestor argv construction and output parsing, checked against a
fake `cosign` executable rather than the real binary -- these tests never
touch the network or Fulcio/Rekor.

`CosignAttestor` runs `cosign` inside its own `TemporaryDirectory`, which
is gone by the time `sign()`/`verify()` return, so a fake script that
needs to hand content back to the test must `cp` it out to a path
provided up front (`capture_to`), not leave the test to read it back
afterward.
"""

import json
import stat
from pathlib import Path

import pytest

from babi.attest.cosign import CosignAttestor, CosignError, rekor_log_index_from_bundle
from babi.attest.dsse import DSSE_PAYLOAD_TYPE_IN_TOTO, pae
from babi.canon import canonical_json

STATEMENT = {"_type": "https://in-toto.io/Statement/v1", "subject": [], "predicateType": "x", "predicate": {}}


def _write_fake_cosign(path: Path, script: str) -> str:
    path.write_text(f"#!/bin/sh\nset -e\n{script}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def _sign_script(
    argv_log: Path,
    bundle_contents: dict,
    *,
    capture_statement_to: Path | None = None,
) -> str:
    """A fake `cosign sign-blob`: records its own argv to `argv_log`,
    optionally copies the statement file (the last argv token) out to
    `capture_statement_to` while it still exists, writes `bundle_contents`
    to whatever path follows `--bundle`, and exits 0."""
    capture_line = f'cp -- "$last" {capture_statement_to}\n' if capture_statement_to is not None else ""
    return f"""
echo "$@" > {argv_log}
bundle_path=""
prev=""
last=""
for arg in "$@"; do
  if [ "$prev" = "--bundle" ]; then
    bundle_path="$arg"
  fi
  prev="$arg"
  last="$arg"
done
{capture_line}cat > "$bundle_path" <<'EOF'
{json.dumps(bundle_contents)}
EOF
exit 0
"""


def _verify_script(argv_log: Path, *, capture_bundle_to: Path | None = None, exit_code: int = 0) -> str:
    """A fake `cosign verify-blob`: records its own argv, optionally
    copies the `--bundle` file's contents out to `capture_bundle_to`
    while it still exists, and exits `exit_code`."""
    capture_line = ""
    if capture_bundle_to is not None:
        capture_line = 'if [ -n "$bundle_path" ]; then cp -- "$bundle_path" ' + str(capture_bundle_to) + "; fi\n"
    return f"""
echo "$@" > {argv_log}
bundle_path=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "--bundle" ]; then bundle_path="$arg"; fi
  prev="$arg"
done
{capture_line}exit {exit_code}
"""


# -- sign() -------------------------------------------------------------------


def test_sign_blob_argv_shape_is_yes_bundle_then_statement(tmp_path: Path) -> None:
    argv_log = tmp_path / "argv.log"
    bundle = {"messageSignature": {"signature": "c2ln"}, "verificationMaterial": {"tlogEntries": [{"logIndex": "42"}]}}
    fake = _write_fake_cosign(tmp_path / "cosign", _sign_script(argv_log, bundle))

    attestor = CosignAttestor(
        certificate_identity_regexp="unused", certificate_oidc_issuer_regexp="unused", cosign_path=fake
    )
    result = attestor.sign(STATEMENT)

    argv = argv_log.read_text().split()
    assert argv[0] == "sign-blob"
    assert "--yes" in argv
    assert "--bundle" in argv
    # --key is NOT passed by default: omitting it is what triggers cosign's own keyless flow
    assert "--key" not in argv
    # the statement file is the last argv element
    assert argv[-1].endswith("statement.dsse-pae")

    assert result.envelope.signatures[0].sig == "c2ln"
    assert result.bundle == bundle
    assert result.rekor_log_index == 42


def test_sign_signs_the_dsse_pae_bytes_not_the_bare_statement(tmp_path: Path) -> None:
    """The blob cosign actually signs must be PAE(payload_type, canonical
    statement bytes) -- genuine DSSE, not a raw-blob signature over the
    Statement's bare JSON."""
    argv_log = tmp_path / "argv.log"
    captured = tmp_path / "captured-statement-bytes"
    bundle = {"messageSignature": {"signature": "c2ln"}}
    fake = _write_fake_cosign(tmp_path / "cosign", _sign_script(argv_log, bundle, capture_statement_to=captured))

    attestor = CosignAttestor(certificate_identity_regexp="u", certificate_oidc_issuer_regexp="u", cosign_path=fake)
    attestor.sign(STATEMENT)

    expected_bytes = pae(DSSE_PAYLOAD_TYPE_IN_TOTO, canonical_json(STATEMENT))
    assert captured.read_bytes() == expected_bytes


def test_key_path_adds_key_flag_before_bundle(tmp_path: Path) -> None:
    argv_log = tmp_path / "argv.log"
    bundle = {"messageSignature": {"signature": "c2ln"}}
    fake = _write_fake_cosign(tmp_path / "cosign", _sign_script(argv_log, bundle))

    attestor = CosignAttestor(
        certificate_identity_regexp="u", certificate_oidc_issuer_regexp="u", cosign_path=fake, key_path="/tmp/my.key"
    )
    attestor.sign(STATEMENT)
    argv = argv_log.read_text().split()
    assert "--key" in argv
    assert argv[argv.index("--key") + 1] == "/tmp/my.key"


def test_sign_nonzero_exit_raises_cosign_error_with_stderr(tmp_path: Path) -> None:
    fake = _write_fake_cosign(tmp_path / "cosign", 'echo "boom: bad identity" >&2\nexit 1\n')
    attestor = CosignAttestor(certificate_identity_regexp="u", certificate_oidc_issuer_regexp="u", cosign_path=fake)
    with pytest.raises(CosignError, match="boom: bad identity"):
        attestor.sign(STATEMENT)


def test_sign_missing_bundle_file_raises_cosign_error(tmp_path: Path) -> None:
    # exits 0 but never writes the --bundle file it was asked for
    fake = _write_fake_cosign(tmp_path / "cosign", "exit 0\n")
    attestor = CosignAttestor(certificate_identity_regexp="u", certificate_oidc_issuer_regexp="u", cosign_path=fake)
    with pytest.raises(CosignError, match="no bundle"):
        attestor.sign(STATEMENT)


def test_sign_unparseable_bundle_raises_cosign_error(tmp_path: Path) -> None:
    script = """
bundle_path=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "--bundle" ]; then bundle_path="$arg"; fi
  prev="$arg"
done
echo 'not json' > "$bundle_path"
exit 0
"""
    fake = _write_fake_cosign(tmp_path / "cosign", script)
    attestor = CosignAttestor(certificate_identity_regexp="u", certificate_oidc_issuer_regexp="u", cosign_path=fake)
    with pytest.raises(CosignError, match="unparseable"):
        attestor.sign(STATEMENT)


def test_sign_falls_back_to_stdout_signature_when_bundle_has_none(tmp_path: Path) -> None:
    script = """
bundle_path=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "--bundle" ]; then bundle_path="$arg"; fi
  prev="$arg"
done
echo '{}' > "$bundle_path"
printf '%s' "c3Rkb3V0LXNpZw=="
exit 0
"""
    fake = _write_fake_cosign(tmp_path / "cosign", script)
    attestor = CosignAttestor(certificate_identity_regexp="u", certificate_oidc_issuer_regexp="u", cosign_path=fake)
    result = attestor.sign(STATEMENT)
    assert result.envelope.signatures[0].sig == "c3Rkb3V0LXNpZw=="


def test_sign_no_signature_anywhere_raises(tmp_path: Path) -> None:
    script = """
bundle_path=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "--bundle" ]; then bundle_path="$arg"; fi
  prev="$arg"
done
echo '{}' > "$bundle_path"
exit 0
"""
    fake = _write_fake_cosign(tmp_path / "cosign", script)
    attestor = CosignAttestor(certificate_identity_regexp="u", certificate_oidc_issuer_regexp="u", cosign_path=fake)
    with pytest.raises(CosignError, match="no signature"):
        attestor.sign(STATEMENT)


def test_sign_missing_binary_raises_cosign_error() -> None:
    attestor = CosignAttestor(
        certificate_identity_regexp="u", certificate_oidc_issuer_regexp="u", cosign_path="/no/such/cosign-binary"
    )
    with pytest.raises(CosignError, match="could not execute"):
        attestor.sign(STATEMENT)


def test_sign_timeout_raises_cosign_error(tmp_path: Path) -> None:
    fake = _write_fake_cosign(tmp_path / "cosign", "sleep 5\nexit 0\n")
    attestor = CosignAttestor(
        certificate_identity_regexp="u", certificate_oidc_issuer_regexp="u", cosign_path=fake, timeout=0.2
    )
    with pytest.raises(CosignError, match="timed out"):
        attestor.sign(STATEMENT)


# -- verify() -----------------------------------------------------------------


def test_verify_blob_argv_uses_identity_and_issuer_regexp(tmp_path: Path) -> None:
    argv_log = tmp_path / "argv.log"
    fake = _write_fake_cosign(tmp_path / "cosign", _verify_script(argv_log))
    attestor = CosignAttestor(
        certificate_identity_regexp="^https://github.com/.*$",
        certificate_oidc_issuer_regexp="^https://token.actions.githubusercontent.com$",
        cosign_path=fake,
    )
    ok = attestor.verify(STATEMENT, {"some": "bundle"})
    assert ok is True

    argv = argv_log.read_text().split()
    assert argv[0] == "verify-blob"
    assert "--bundle" in argv
    assert "--certificate-identity-regexp" in argv
    assert argv[argv.index("--certificate-identity-regexp") + 1] == "^https://github.com/.*$"
    assert "--certificate-oidc-issuer-regexp" in argv
    assert argv[argv.index("--certificate-oidc-issuer-regexp") + 1] == "^https://token.actions.githubusercontent.com$"
    # keyless verification must never pass --key
    assert "--key" not in argv


def test_verify_blob_writes_the_bundle_it_was_given(tmp_path: Path) -> None:
    argv_log = tmp_path / "argv.log"
    captured_bundle = tmp_path / "captured-bundle.json"
    fake = _write_fake_cosign(tmp_path / "cosign", _verify_script(argv_log, capture_bundle_to=captured_bundle))
    attestor = CosignAttestor(certificate_identity_regexp="u", certificate_oidc_issuer_regexp="u", cosign_path=fake)
    bundle = {"messageSignature": {"signature": "abc"}, "nested": {"n": 1}}
    attestor.verify(STATEMENT, bundle)

    assert json.loads(captured_bundle.read_text()) == bundle


def test_verify_nonzero_exit_returns_false_not_raise(tmp_path: Path) -> None:
    fake = _write_fake_cosign(tmp_path / "cosign", 'echo "signature mismatch" >&2\nexit 1\n')
    attestor = CosignAttestor(certificate_identity_regexp="u", certificate_oidc_issuer_regexp="u", cosign_path=fake)
    assert attestor.verify(STATEMENT, {"bundle": True}) is False


def test_verify_key_mode_uses_public_key_not_identity_regexp(tmp_path: Path) -> None:
    argv_log = tmp_path / "argv.log"
    fake = _write_fake_cosign(tmp_path / "cosign", _verify_script(argv_log))
    attestor = CosignAttestor(
        certificate_identity_regexp="u", certificate_oidc_issuer_regexp="u", cosign_path=fake, key_path="/tmp/cosign.key"
    )
    attestor.verify(STATEMENT, {})
    argv = argv_log.read_text().split()
    assert "--key" in argv
    assert argv[argv.index("--key") + 1] == "/tmp/cosign.pub"
    assert "--certificate-identity-regexp" not in argv


# -- constructor validation -----------------------------------------------------


@pytest.mark.parametrize("identity,issuer", [("", "issuer"), ("identity", ""), ("", "")])
def test_constructor_rejects_empty_identity_or_issuer(identity: str, issuer: str) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        CosignAttestor(certificate_identity_regexp=identity, certificate_oidc_issuer_regexp=issuer)


# -- rekor_log_index_from_bundle -----------------------------------------------


def test_rekor_log_index_from_bundle_happy_path() -> None:
    bundle = {"verificationMaterial": {"tlogEntries": [{"logIndex": "12345"}]}}
    assert rekor_log_index_from_bundle(bundle) == 12345


@pytest.mark.parametrize(
    "bundle",
    [
        None,
        {},
        {"verificationMaterial": {}},
        {"verificationMaterial": {"tlogEntries": []}},
        {"verificationMaterial": {"tlogEntries": [{"logIndex": "not-a-number"}]}},
        {"verificationMaterial": {"tlogEntries": [{}]}},
        "not-a-dict",
    ],
)
def test_rekor_log_index_from_bundle_never_raises_returns_none(bundle: object) -> None:
    assert rekor_log_index_from_bundle(bundle) is None
