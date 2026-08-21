"""TargetSpec -- how to connect to an MCP server, and nothing else.

Companion to SandboxManifest (manifest.py): a sandbox says what is
disposable, a target says how to connect. Neither can encode what an
operation should do.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TargetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    transport: Literal["stdio", "streamable-http"]
    command: list[str] | None = None
    endpoint: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    network: Literal["none", "bridge"]
    package_or_endpoint: str

    @model_validator(mode="after")
    def _exactly_one_transport_target(self) -> "TargetSpec":
        if (self.command is None) == (self.endpoint is None):
            raise ValueError("exactly one of `command` or `endpoint` must be set")
        return self
