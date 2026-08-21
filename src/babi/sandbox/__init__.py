"""Sandbox lifecycle, frozen top-20 corpus data, and the corpus runner.

Companion to `babi.target` (how to connect) and `babi.manifest` (what is
disposable): this package is *when* and *how much* -- provisioning and
tearing down disposable environments generically, and running discovery
plus a deterministic first-call check across the corpus with bounded
concurrency. Nothing here is keyed on a tool or server name; per-sandbox
specifics live in data (`sandboxes/`, `corpus/`), never in a Python branch.
"""

from __future__ import annotations

from babi.sandbox.corpus import Corpus, CorpusEntry, PopulationInclusion, load_corpus
from babi.sandbox.credentials import MissingCredentialsError, missing_credentials, redact, require_credentials
from babi.sandbox.lifecycle import (
    LifecycleResult,
    SandboxLifecycleAdapter,
    load_lifecycle_adapter,
    render_command,
    run_setup,
    run_teardown,
)
from babi.sandbox.runner import (
    CorpusRunDenominator,
    CorpusRunSummary,
    RunnerConfig,
    TargetRunOutcome,
    TargetRunResult,
    ToolPlanResult,
    ToolPlanStatus,
    run_corpus,
    run_targets,
    valid_first_call_rate,
)
from babi.sandbox.synthesize import validates

__all__ = [
    "Corpus",
    "CorpusEntry",
    "CorpusRunDenominator",
    "CorpusRunSummary",
    "LifecycleResult",
    "MissingCredentialsError",
    "PopulationInclusion",
    "RunnerConfig",
    "SandboxLifecycleAdapter",
    "TargetRunOutcome",
    "TargetRunResult",
    "ToolPlanResult",
    "ToolPlanStatus",
    "load_corpus",
    "load_lifecycle_adapter",
    "missing_credentials",
    "redact",
    "render_command",
    "require_credentials",
    "run_corpus",
    "run_setup",
    "run_targets",
    "run_teardown",
    "valid_first_call_rate",
    "validates",
]
