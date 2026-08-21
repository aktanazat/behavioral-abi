import pytest
from pydantic import ValidationError

from babi.manifest import SandboxManifest


def _base(**overrides: object) -> dict:
    base = {"target_label": "filesystem-fixture"}
    base.update(overrides)
    return base


def test_minimal_manifest_validates() -> None:
    manifest = SandboxManifest.model_validate(_base())
    assert manifest.target_label == "filesystem-fixture"
    assert manifest.account_id is None
    assert manifest.credential_env_vars == []


def test_rubric_allowed_disposable_fields_accepted() -> None:
    manifest = SandboxManifest.model_validate(
        _base(
            account_id="acct-123",
            region="us-east-1",
            resource_prefix="abi-test-",
            spend_limit=5.0,
            teardown_deadline="2026-08-21T00:00:00Z",
            credential_env_vars=["STRIPE_TEST_KEY"],
        )
    )
    assert manifest.spend_limit == 5.0
    assert manifest.credential_env_vars == ["STRIPE_TEST_KEY"]


def test_credential_values_cannot_hide_in_environment_names() -> None:
    with pytest.raises(ValidationError):
        SandboxManifest.model_validate(_base(credential_env_vars=["STRIPE_TEST_KEY=secret"]))


def test_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        SandboxManifest.model_validate(_base(some_random_field="x"))


def test_connection_info_is_not_representable() -> None:
    # Connection info belongs to TargetSpec (target.py), not the sandbox
    # scope manifest -- this is the split the rubric's boundary requires.
    for field in ("transport", "command", "endpoint", "network", "env"):
        with pytest.raises(ValidationError):
            SandboxManifest.model_validate(_base(**{field: "irrelevant"}))


def test_operation_keyed_field_is_not_representable() -> None:
    # The rubric's whole point: nothing operation-specific fits the schema.
    with pytest.raises(ValidationError):
        SandboxManifest.model_validate(_base(expected_tool_behavior={"create_issue": "idempotent"}))


def test_target_label_required() -> None:
    with pytest.raises(ValidationError):
        SandboxManifest.model_validate({})
