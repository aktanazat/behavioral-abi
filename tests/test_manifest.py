"""babi.manifest: environment-scope policy enforcement."""

from decimal import Decimal
from typing import Any

import pytest

from babi.manifest import ManifestPolicyError, SandboxManifest, check_manifest, load_manifest_file


def _valid_manifest_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "target_label": "fixture-sandbox",
        "account_resource_prefix": "babi-test-",
        "region": "us-east-1",
        "spend_limit_usd": "5.00",
        "teardown_deadline": "2026-08-21T00:00:00+00:00",
        "credential_env_vars": ["FIXTURE_API_KEY", "FIXTURE_WEBHOOK_SECRET"],
    }
    data.update(overrides)
    return data


def test_valid_manifest_parses_generic_scope_fields():
    manifest = check_manifest(_valid_manifest_data())
    assert isinstance(manifest, SandboxManifest)
    assert manifest.target_label == "fixture-sandbox"
    assert manifest.account_resource_prefix == "babi-test-"
    assert manifest.spend_limit_usd == Decimal("5.00")
    assert manifest.credential_env_vars == ("FIXTURE_API_KEY", "FIXTURE_WEBHOOK_SECRET")


def test_manifest_omits_region_when_not_given():
    manifest = check_manifest(_valid_manifest_data(region=None))
    assert manifest.region is None


def test_credential_env_vars_hold_only_names_never_values():
    # A dict shaped like a name/value pair does not fit the `str` element
    # type at all, so a value can never ride along with a credential name.
    with pytest.raises(ManifestPolicyError) as excinfo:
        check_manifest(_valid_manifest_data(credential_env_vars=[{"name": "FIXTURE_TOKEN", "value": "s3cr3t"}]))
    assert excinfo.value.violations[0].kind == "string_type"


def test_manifest_rejects_lowercase_credential_env_var_name():
    with pytest.raises(ManifestPolicyError) as excinfo:
        check_manifest(_valid_manifest_data(credential_env_vars=["not_an_env_name"]))
    violation = excinfo.value.violations[0]
    assert violation.field.startswith("credential_env_vars")
    assert violation.kind == "string_pattern_mismatch"


def test_manifest_requires_timezone_aware_teardown_deadline():
    with pytest.raises(ManifestPolicyError) as excinfo:
        check_manifest(_valid_manifest_data(teardown_deadline="2026-08-21T00:00:00"))
    assert excinfo.value.violations[0].field == "teardown_deadline"


def test_manifest_rejects_negative_spend_limit():
    with pytest.raises(ManifestPolicyError) as excinfo:
        check_manifest(_valid_manifest_data(spend_limit_usd="-1"))
    assert excinfo.value.violations[0].field == "spend_limit_usd"


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "tool_overrides",
        "operation_assertions",
        "inverse_mapping",
        "cleanup_hooks",
        "expected_values",
    ],
)
def test_manifest_rejects_operation_specific_fields(forbidden_key: str):
    """No field name is special-cased: anything outside the fixed allow-list
    is rejected the same generic way, whether it looks like a tool override,
    an assertion, a cleanup hook, or an expected-value fixture."""
    data = _valid_manifest_data(**{forbidden_key: {"anything": "goes"}})
    with pytest.raises(ManifestPolicyError) as excinfo:
        check_manifest(data)
    violations_by_field = {v.field: v for v in excinfo.value.violations}
    assert forbidden_key in violations_by_field
    assert violations_by_field[forbidden_key].kind == "extra_forbidden"


def test_load_manifest_file_reads_json_and_checks_policy(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text('{"target_label": "fixture", "account_resource_prefix": "p-", '
                     '"spend_limit_usd": "0", "teardown_deadline": "2026-08-21T00:00:00Z"}')
    manifest = load_manifest_file(path)
    assert manifest.target_label == "fixture"
    assert manifest.credential_env_vars == ()


def test_load_manifest_file_propagates_policy_violations(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text('{"target_label": "fixture", "account_resource_prefix": "p-", '
                     '"spend_limit_usd": "0", "teardown_deadline": "2026-08-21T00:00:00Z", '
                     '"tool_specific_override": {"forced_outcome": "always_succeed"}}')
    with pytest.raises(ManifestPolicyError):
        load_manifest_file(path)
