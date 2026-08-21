import pytest
from pydantic import ValidationError

from babi.target import TargetSpec


def _base(**overrides: object) -> dict:
    base = {
        "name": "filesystem",
        "transport": "stdio",
        "command": ["mcp-server-filesystem"],
        "args": ["/workspace"],
        "network": "none",
        "package_or_endpoint": "@modelcontextprotocol/server-filesystem",
    }
    base.update(overrides)
    return base


def test_minimal_stdio_target_validates() -> None:
    target = TargetSpec.model_validate(_base())
    assert target.command == ["mcp-server-filesystem"]
    assert target.args == ["/workspace"]
    assert target.endpoint is None


def test_streamable_http_target_validates() -> None:
    target = TargetSpec.model_validate(
        _base(transport="streamable-http", command=None, endpoint="https://example.test/mcp")
    )
    assert target.endpoint == "https://example.test/mcp"


def test_requires_exactly_one_of_command_or_endpoint_neither_set() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        TargetSpec.model_validate(_base(command=None))


def test_requires_exactly_one_of_command_or_endpoint_both_set() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        TargetSpec.model_validate(_base(endpoint="https://example.test/mcp"))


def test_env_vars_default_empty_and_accept_names_only() -> None:
    target = TargetSpec.model_validate(_base(env_vars=["NODE_ENV"]))
    assert target.env_vars == ["NODE_ENV"]
    assert TargetSpec.model_validate(_base()).env_vars == []


def test_literal_environment_values_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        TargetSpec.model_validate(_base(env={"NODE_ENV": "test"}))


def test_invalid_environment_variable_name_is_forbidden() -> None:
    with pytest.raises(ValidationError):
        TargetSpec.model_validate(_base(env_vars=["NODE_ENV=secret"]))


def test_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        TargetSpec.model_validate(_base(some_random_field="x"))


def test_disposable_scope_field_is_not_representable() -> None:
    # Disposable-scope fields belong to SandboxManifest (manifest.py), not
    # the connection spec -- this is the split the rubric's boundary requires.
    with pytest.raises(ValidationError):
        TargetSpec.model_validate(_base(spend_limit=5.0))


def test_invalid_transport_rejected() -> None:
    with pytest.raises(ValidationError):
        TargetSpec.model_validate(_base(transport="carrier-pigeon"))


def test_invalid_network_rejected() -> None:
    with pytest.raises(ValidationError):
        TargetSpec.model_validate(_base(network="open"))
