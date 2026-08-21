"""TargetSpec -- how to connect to an MCP server, and nothing else.

Companion to SandboxManifest (manifest.py): a sandbox says what is
disposable, a target says how to connect. Neither can encode what an
operation should do.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

EnvironmentVariableName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]


class TargetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    transport: Literal["stdio", "streamable-http"]
    command: list[str] | None = None
    endpoint: str | None = None
    args: list[str] = Field(default_factory=list)
    env_vars: list[EnvironmentVariableName] = Field(default_factory=list)
    network: Literal["none", "bridge"]
    package_or_endpoint: str

    @model_validator(mode="after")
    def _exactly_one_transport_target(self) -> TargetSpec:
        if (self.command is None) == (self.endpoint is None):
            raise ValueError("exactly one of `command` or `endpoint` must be set")
        return self
