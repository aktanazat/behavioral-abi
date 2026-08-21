"""SandboxManifest -- the manifest linter.

`extra="forbid"` plus a closed field set makes this the enforcement point
for the rubric's manifest rule: a manifest can say which account is
disposable, but it cannot say what an operation should do, and it cannot
say how to connect (that is TargetSpec's job, in target.py). Only the
rubric-allowed disposable-scope fields are representable here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from babi.target import EnvironmentVariableName


class SandboxManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_label: str
    account_id: str | None = None
    region: str | None = None
    resource_prefix: str | None = None
    spend_limit: float | None = None
    teardown_deadline: str | None = None
    credential_env_vars: list[EnvironmentVariableName] = Field(default_factory=list)
