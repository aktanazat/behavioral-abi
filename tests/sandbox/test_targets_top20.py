"""Validates the top-20 corpus's targets/*.json files: TargetSpec schema,
no embedded secret values, and that pre-existing targets (filesystem,
memory) were preserved untouched."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from babi.target import TargetSpec

REPO_ROOT = Path(__file__).parent.parent.parent
TARGETS_DIR = REPO_ROOT / "targets"

PRESERVED_TARGETS = {"filesystem", "memory"}
NEW_TOP20_TARGETS = {
    "github",
    "playwright",
    "chrome-devtools",
    "context7",
    "fetch",
    "notion",
    "slack",
    "serena",
    "atlassian",
    "supabase",
    "sequential-thinking",
    "gitlab",
    "sentry",
    "stripe",
    "figma",
    "firecrawl",
    "postgres-reference",
    "aws-documentation",
}

_SECRET_SHAPE = re.compile(
    r"(sk_live|sk_test|rk_live|rk_test|ghp|github_pat|xox[bcd]|fco|fcr)[_-][A-Za-z0-9]{16,}"
)


def _target_paths() -> list[Path]:
    return sorted(TARGETS_DIR.glob("*.json"))


def test_every_expected_target_file_is_present() -> None:
    present = {p.stem for p in _target_paths()}
    assert PRESERVED_TARGETS <= present
    assert NEW_TOP20_TARGETS <= present


@pytest.mark.parametrize("path", _target_paths(), ids=lambda p: p.name)
def test_target_file_validates_against_target_spec(path: Path) -> None:
    target = TargetSpec.model_validate_json(path.read_text())
    assert target.name == path.stem
    assert target.package_or_endpoint


def test_preserved_targets_still_use_the_env_vars_field() -> None:
    # Regression guard: these two predate this corpus population and were
    # explicitly required to be preserved, not regenerated.
    for name in PRESERVED_TARGETS:
        data = json.loads((TARGETS_DIR / f"{name}.json").read_text())
        assert "env_vars" in data
        assert "env" not in data, f"{name}.json still has the pre-credential-safety-commit 'env' dict field"


def test_credential_needing_targets_declare_env_vars_never_literal_env() -> None:
    credentialed = {
        "github": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
        "notion": ["NOTION_TOKEN"],
        "slack": ["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"],
        "atlassian": ["JIRA_URL", "JIRA_USERNAME", "JIRA_API_TOKEN", "CONFLUENCE_URL", "CONFLUENCE_USERNAME", "CONFLUENCE_API_TOKEN"],
        "supabase": ["SUPABASE_ACCESS_TOKEN"],
        "gitlab": ["GITLAB_PERSONAL_ACCESS_TOKEN"],
        "sentry": ["SENTRY_ACCESS_TOKEN"],
        "stripe": ["STRIPE_SECRET_KEY"],
        "figma": ["FIGMA_API_KEY"],
        "firecrawl": ["FIRECRAWL_API_KEY"],
    }
    for slug, expected_names in credentialed.items():
        target = TargetSpec.model_validate_json((TARGETS_DIR / f"{slug}.json").read_text())
        assert set(target.env_vars) == set(expected_names), slug


def test_credential_free_targets_declare_no_env_vars() -> None:
    credential_free = {"filesystem", "memory", "fetch", "sequential-thinking", "aws-documentation", "serena", "context7", "playwright", "chrome-devtools", "postgres-reference"}
    for slug in credential_free:
        target = TargetSpec.model_validate_json((TARGETS_DIR / f"{slug}.json").read_text())
        assert target.env_vars == [], slug


def test_github_uses_the_docker_image_not_the_archived_npm_package() -> None:
    target = TargetSpec.model_validate_json((TARGETS_DIR / "github.json").read_text())
    assert target.package_or_endpoint == "ghcr.io/github/github-mcp-server"
    assert "npm" not in " ".join(target.command or [])
    assert "npx" not in (target.command or [])


def test_playwright_uses_the_current_canonical_package() -> None:
    target = TargetSpec.model_validate_json((TARGETS_DIR / "playwright.json").read_text())
    assert target.package_or_endpoint == "@playwright/mcp"


def test_python_reference_servers_use_uvx() -> None:
    for slug in ("fetch", "serena", "aws-documentation", "atlassian"):
        target = TargetSpec.model_validate_json((TARGETS_DIR / f"{slug}.json").read_text())
        assert "uvx" in (target.command or []), slug


def test_postgres_reference_connects_to_the_disposable_local_sandbox_not_a_real_account() -> None:
    target = TargetSpec.model_validate_json((TARGETS_DIR / "postgres-reference.json").read_text())
    connection_string = (target.command or [])[-1]
    assert connection_string == "postgresql://postgres@babi-postgres-sandbox:5432/postgres"
    assert "@babi-postgres-sandbox" in connection_string  # disposable local hostname, never a real host


def test_no_realistic_secret_shaped_token_anywhere_under_targets() -> None:
    offenders: list[str] = []
    for path in _target_paths():
        hits = _SECRET_SHAPE.findall(path.read_text())
        if hits:
            offenders.append(f"{path.relative_to(REPO_ROOT)}: {hits}")
    assert not offenders, offenders


def test_no_target_embeds_a_literal_credential_assignment() -> None:
    # Ordinary non-secret env assignments (e.g. memory.json's pre-existing
    # `MEMORY_FILE_PATH=/tmp/memory.json`) are fine -- this only flags
    # *credential-shaped* names (TOKEN/KEY/SECRET/PASSWORD/CREDENTIAL)
    # embedded as a literal NAME=value, which would mean a secret value
    # sits in the file instead of being referenced by name via env_vars.
    credential_assignment_pattern = re.compile(
        r"\"[A-Z_]*(?:TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL)[A-Z_]*=[^\"]+\""
    )
    for path in _target_paths():
        raw = path.read_text()
        match = credential_assignment_pattern.search(raw)
        assert match is None, f"{path.name}: looks like it embeds a literal credential assignment: {match}"
