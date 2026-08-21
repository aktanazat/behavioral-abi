"""Typed sandbox manifest and its environment-scope policy check.

The model exposes only generic environment-scope fields: a target label, an
account/resource prefix, an optional region, a hard spend limit, a teardown
deadline, and credential environment-variable *names* (never values).
Pydantic's `extra="forbid"` rejects any other key structurally, so there is
no per-field or per-tool-name branch to maintain here: an operation-specific
override/mapping/assertion/cleanup key is rejected the same generic way a
mistyped field name would be, because both are simply "not in the schema."
This is what makes an illegal manifest state unrepresentable rather than
merely checked.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

#: Environment-variable identifier syntax (POSIX-ish: upper-case, digits, underscore).
_ENV_VAR_NAME_PATTERN = r"^[A-Z_][A-Z0-9_]*$"

EnvVarName = Annotated[str, StringConstraints(pattern=_ENV_VAR_NAME_PATTERN, min_length=1)]


class SandboxManifest(BaseModel):
    """Generic environment scope for one disposable sandbox target.

    Every field describes *where* a probe may run and how it is bounded,
    never *what* any operation should do. Fields are limited to the allowed
    list in `RUBRIC.md`'s "no per-tool logic" section.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    target_label: Annotated[str, Field(min_length=1)]
    account_resource_prefix: Annotated[str, Field(min_length=1)]
    region: str | None = None
    spend_limit_usd: Annotated[Decimal, Field(ge=0)]
    teardown_deadline: datetime
    credential_env_vars: tuple[EnvVarName, ...] = ()

    @field_validator("teardown_deadline")
    @classmethod
    def _teardown_deadline_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("teardown_deadline must be timezone-aware")
        return value


@dataclass(frozen=True, slots=True)
class ManifestViolation:
    """One reason a raw manifest document failed the policy check."""

    field: str
    kind: str
    reason: str


class ManifestPolicyError(Exception):
    """Raised when raw manifest data fails the environment-scope policy."""

    def __init__(self, violations: Sequence[ManifestViolation]) -> None:
        self.violations = tuple(violations)
        message = "; ".join(f"{v.field}: {v.reason}" for v in self.violations) or "invalid sandbox manifest"
        super().__init__(message)


def check_manifest(data: Mapping[str, Any]) -> SandboxManifest:
    """Validate raw manifest data against the environment-scope policy.

    Returns the parsed manifest on success. Raises `ManifestPolicyError` for
    both malformed generic fields (bad type, missing field, non-timezone
    deadline, ...) and any field outside the allowed schema, since both are
    reported through the identical `extra="forbid"` / field-validation path.
    """
    try:
        return SandboxManifest.model_validate(data)
    except ValidationError as exc:
        violations = [
            ManifestViolation(
                field=".".join(str(part) for part in error["loc"]) or "<manifest>",
                kind=error["type"],
                reason=error["msg"],
            )
            for error in exc.errors()
        ]
        raise ManifestPolicyError(violations) from exc


def load_manifest_file(path: Path) -> SandboxManifest:
    """Read a JSON sandbox manifest from `path` and run the policy check on it."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return check_manifest(data)


__all__ = [
    "EnvVarName",
    "ManifestPolicyError",
    "ManifestViolation",
    "SandboxManifest",
    "check_manifest",
    "load_manifest_file",
]
