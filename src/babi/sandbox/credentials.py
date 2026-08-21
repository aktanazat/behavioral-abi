"""Generic credential-safety helpers shared by the corpus runner and the
sandbox lifecycle executor.

Nothing here is keyed on a specific credential's *name*: every function
takes the list of environment-variable names a target/adapter declares
(`TargetSpec.env_vars`, `SandboxManifest.credential_env_vars`, or a
lifecycle adapter's own `env_vars`) and treats every name identically.
Secret *values* are read from the ambient process environment at call time
and never appear in this module's return values except inside `redact`,
which exists specifically to strip them back out of arbitrary text before
it is logged, recorded as evidence, or returned to a caller.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping

_REDACTED = "<redacted>"


class MissingCredentialsError(RuntimeError):
    """Raised when one or more declared environment-variable names have no
    value in the ambient process environment. Carries the exact missing
    *names* (never a value) so a caller can classify this distinctly from
    any other failure -- RUBRIC.md/IMPLEMENTATION-PLAN.md: unavailable auth
    is never a planner or execution failure."""

    def __init__(self, missing: Iterable[str]) -> None:
        self.missing: tuple[str, ...] = tuple(sorted(set(missing)))
        super().__init__(f"missing environment variables: {', '.join(self.missing)}")


def missing_credentials(names: Iterable[str], environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Every name in `names` absent from `environ` (default: the real
    process environment), sorted. Empty means every declared credential
    resolves."""
    env = environ if environ is not None else os.environ
    return tuple(sorted({name for name in names if name not in env}))


def require_credentials(names: Iterable[str], environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Resolve every name in `names` from `environ`, or raise
    `MissingCredentialsError` listing every name that did not resolve.
    Never partially resolves: either every declared name comes back with
    its value, or none do."""
    env = environ if environ is not None else os.environ
    names = list(names)
    missing = missing_credentials(names, env)
    if missing:
        raise MissingCredentialsError(missing)
    return {name: env[name] for name in names}


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace every occurrence of every non-empty string in `secrets`
    with a fixed placeholder. Longer secrets are replaced first so one
    secret that happens to be a substring of another cannot leave a
    partial value exposed."""
    ordered = sorted({s for s in secrets if s}, key=len, reverse=True)
    redacted = text
    for secret in ordered:
        redacted = redacted.replace(secret, _REDACTED)
    return redacted


def redact_using(text: str, environ_subset: Mapping[str, str]) -> str:
    """`redact` convenience for an already-resolved `{name: value}`
    mapping, e.g. the return of `require_credentials`."""
    return redact(text, environ_subset.values())


__all__ = [
    "MissingCredentialsError",
    "missing_credentials",
    "redact",
    "redact_using",
    "require_credentials",
]
