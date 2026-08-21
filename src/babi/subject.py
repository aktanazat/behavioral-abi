"""Subject identity per the plan's identity scheme.

Fields are canonical-JSON-wrapped into one object before hashing rather than
concatenated as strings: naive concatenation of variable-length fields is
not injective (e.g. "ab"+"c" == "a"+"bc"), while wrapping in named JSON
properties is. `None` fields participate as explicit JSON nulls, so absent
identity is distinguishable from any string value.
"""

from __future__ import annotations

from babi import canon


def server_subject(
    registry_name: str | None,
    registry_version: str | None,
    package_or_endpoint: str,
    runtime_artifact_digest: str | None,
    protocol_revision: str,
) -> str:
    """Identity digest for a server session, per IMPLEMENTATION-PLAN.md."""
    return canon.digest(
        {
            "registry_name": registry_name,
            "registry_version": registry_version,
            "package_or_endpoint": package_or_endpoint,
            "runtime_artifact_digest": runtime_artifact_digest,
            "protocol_revision": protocol_revision,
        }
    )


def tool_subject(server_subject: str, tool_definition: dict) -> str:
    """Identity digest for one tool, scoped to its declaring server session.

    `tool_definition` is the verbatim wire object for this tool from
    `tools/list` (name, schemas, annotations, etc.), hashed as-is via
    canonical JSON -- never through a per-tool-name special case.
    """
    return canon.digest({"server_subject": server_subject, "tool_definition": tool_definition})
