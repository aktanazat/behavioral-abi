"""Validates sandboxes/*.json (SandboxManifest templates) and
sandboxes/lifecycle/*.json (SandboxLifecycleAdapter configs), and asserts
no secret value hides anywhere under sandboxes/**."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from babi.manifest import SandboxManifest
from babi.sandbox.lifecycle import SandboxLifecycleAdapter

REPO_ROOT = Path(__file__).parent.parent.parent
SANDBOXES_DIR = REPO_ROOT / "sandboxes"
LIFECYCLE_DIR = SANDBOXES_DIR / "lifecycle"

REQUIRED_TARGET_LABELS = {"stripe-test-mode", "github-disposable-repo", "cloudflare-free-tier"}

_SECRET_SHAPE = re.compile(
    r"(sk_live|sk_test|rk_live|rk_test|ghp|github_pat|xox[bcd]|fco|fcr|AKIA|AIza)[_-]?[A-Za-z0-9]{16,}"
)


def _manifest_paths() -> list[Path]:
    return sorted(SANDBOXES_DIR.glob("*.json"))


def _lifecycle_paths() -> list[Path]:
    return sorted(LIFECYCLE_DIR.glob("*.json"))


def test_all_three_required_sandbox_manifests_exist() -> None:
    labels = {SandboxManifest.model_validate_json(p.read_text()).target_label for p in _manifest_paths()}
    assert REQUIRED_TARGET_LABELS <= labels


@pytest.mark.parametrize("path", _manifest_paths(), ids=lambda p: p.name)
def test_manifest_validates_against_sandbox_manifest(path: Path) -> None:
    manifest = SandboxManifest.model_validate_json(path.read_text())
    assert manifest.target_label


@pytest.mark.parametrize("path", _manifest_paths(), ids=lambda p: p.name)
def test_manifest_has_a_hard_zero_spend_limit(path: Path) -> None:
    manifest = SandboxManifest.model_validate_json(path.read_text())
    assert manifest.spend_limit == 0


@pytest.mark.parametrize("path", _manifest_paths(), ids=lambda p: p.name)
def test_manifest_declares_a_resource_prefix(path: Path) -> None:
    manifest = SandboxManifest.model_validate_json(path.read_text())
    assert manifest.resource_prefix and manifest.resource_prefix.startswith("babi-")


@pytest.mark.parametrize("path", _manifest_paths(), ids=lambda p: p.name)
def test_manifest_teardown_deadline_is_a_template_placeholder(path: Path) -> None:
    # A *template* has no live run to date a deadline against -- null is
    # the honest placeholder, filled in only when a real run provisions
    # the sandbox and knows its start time.
    manifest = SandboxManifest.model_validate_json(path.read_text())
    assert manifest.teardown_deadline is None


@pytest.mark.parametrize("path", _manifest_paths(), ids=lambda p: p.name)
def test_manifest_declares_at_least_one_credential_env_var(path: Path) -> None:
    manifest = SandboxManifest.model_validate_json(path.read_text())
    assert len(manifest.credential_env_vars) >= 1


@pytest.mark.parametrize("path", _lifecycle_paths(), ids=lambda p: p.name)
def test_lifecycle_adapter_validates(path: Path) -> None:
    adapter = SandboxLifecycleAdapter.model_validate_json(path.read_text())
    assert adapter.target_label
    assert adapter.timeout_seconds > 0


def test_every_lifecycle_adapter_has_a_matching_manifest_or_is_the_local_postgres_demo() -> None:
    manifest_labels = {SandboxManifest.model_validate_json(p.read_text()).target_label for p in _manifest_paths()}
    for path in _lifecycle_paths():
        adapter = SandboxLifecycleAdapter.model_validate_json(path.read_text())
        assert adapter.target_label in manifest_labels or adapter.target_label == "postgres-reference"


def test_postgres_lifecycle_needs_no_credentials() -> None:
    # A fully local, self-provisioned sandbox: nothing to authenticate.
    adapter = SandboxLifecycleAdapter.model_validate_json((LIFECYCLE_DIR / "postgres-reference.json").read_text())
    assert adapter.env_vars == []


def test_stripe_and_cloudflare_have_no_teardown_command() -> None:
    # Neither creates a per-run resource: Stripe test mode is
    # feature-complete and self-expiring, Cloudflare free tier is standing
    # account-level infrastructure, not something this corpus provisions.
    for label in ("stripe-test-mode", "cloudflare-free-tier"):
        adapter = SandboxLifecycleAdapter.model_validate_json((LIFECYCLE_DIR / f"{label}.json").read_text())
        assert adapter.teardown_command is None


def test_github_lifecycle_has_a_matched_setup_and_teardown_pair() -> None:
    adapter = SandboxLifecycleAdapter.model_validate_json((LIFECYCLE_DIR / "github-disposable-repo.json").read_text())
    assert adapter.setup_command is not None
    assert adapter.teardown_command is not None
    assert adapter.setup_command[:3] == ["gh", "repo", "create"]
    assert adapter.teardown_command[:3] == ["gh", "repo", "delete"]


def test_no_realistic_secret_shaped_token_anywhere_under_sandboxes() -> None:
    offenders: list[str] = []
    for path in [*_manifest_paths(), *_lifecycle_paths()]:
        hits = _SECRET_SHAPE.findall(path.read_text())
        if hits:
            offenders.append(f"{path.relative_to(REPO_ROOT)}: {hits}")
    assert not offenders, offenders


def test_no_manifest_or_lifecycle_file_embeds_a_literal_credential_assignment() -> None:
    # credential_env_vars / env_vars must be bare names; nothing under
    # sandboxes/** should contain a credential-shaped NAME=value string.
    # Ordinary non-secret assignments a lifecycle command legitimately
    # needs (e.g. postgres-reference.json's `POSTGRES_HOST_AUTH_METHOD=trust`)
    # are fine -- this only flags TOKEN/KEY/SECRET/PASSWORD/CREDENTIAL-shaped
    # names, matched anywhere in the text (not just as a whole JSON string
    # token), so an assignment embedded inside a larger shell command is
    # caught too.
    credential_assignment_pattern = re.compile(
        r"[A-Z_]*(?:TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL)[A-Z_]*=\S"
    )
    for path in [*_manifest_paths(), *_lifecycle_paths()]:
        raw = path.read_text()
        data = json.loads(raw)
        for key in ("credential_env_vars", "env_vars"):
            for value in data.get(key, []) or []:
                assert "=" not in value, f"{path.name}: {key} entry {value!r} looks like NAME=value, not a bare name"
        match = credential_assignment_pattern.search(raw)
        assert match is None, f"{path.name}: looks like it embeds a literal credential assignment: {match}"
