"""Best-effort runtime-artifact digests, gathered generically per target.

The only lookup key is a value the target already declares
(`package_or_endpoint`), never a name/version-string sniff of a *specific*
server. A lookup that fails (registry unreachable, target isn't
npm-distributed) returns `None`; runtime digests are supplementary
evidence, never a hard requirement for a successful probe.
"""

from __future__ import annotations

import subprocess

from babi.target import TargetSpec


def npm_package_version(package_name: str) -> str | None:
    """The version `npm` currently resolves `package_name` to, or `None`."""
    return _npm_view(package_name, "version")


def npm_package_integrity(package_name: str) -> str | None:
    """The published tarball's Subresource-Integrity digest for whatever
    `npm` currently resolves `package_name` to, or `None`."""
    return _npm_view(package_name, "dist.integrity")


def _npm_view(package_name: str, field: str) -> str | None:
    try:
        result = subprocess.run(
            ["npm", "view", package_name, field],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def gather_runtime_digests(target: TargetSpec) -> dict[str, str | None]:
    """Best-effort runtime facts for `target`, gathered from whatever generic
    identity field it declares -- never from its name."""
    npm_version = npm_package_version(target.package_or_endpoint)
    npm_integrity = npm_package_integrity(target.package_or_endpoint)
    return {
        "npm_package_version": npm_version,
        "npm_package_integrity": npm_integrity,
        "artifact_digest": npm_integrity,
    }


__all__ = ["gather_runtime_digests", "npm_package_integrity", "npm_package_version"]
