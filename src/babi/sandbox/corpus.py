"""corpus/top20.json -- the frozen top-20 MCP server population.

Every field on `CorpusEntry` is a verbatim transcription from
`research/top-servers/SUMMARY.md` (see IMPLEMENTATION-PLAN.md's "Top-20
report population" rule and RUBRIC.md's pass condition 2): popularity
evidence, transport, auth, annotation coverage, and sandboxability are
copied text, never a number this codebase computed, estimated, or rounded.
`load_corpus` is the only parser; a `corpus/*.json` file that doesn't fit
this shape is a validation error, not silently accepted data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_CORPUS_PATH = Path("corpus") / "top20.json"


class PopulationInclusion(BaseModel):
    """Whether this entry is carried into the runnable corpus, and why --
    grounded in IMPLEMENTATION-PLAN.md's population rule (publicly
    installable, maintained/first-party, popularity evidence, probeable
    without production data, exact version recorded), never in a per-tool
    judgment."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["included", "excluded"]
    reason: str


class CorpusEntry(BaseModel):
    """One row of the frozen top-20 census."""

    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1, le=20)
    name: str
    repo: str | None
    package_or_endpoint: str
    distribution: Literal["npm", "pypi", "docker-image", "hosted-endpoint"]
    popularity_evidence: str
    transport: str
    auth: str
    annotation_coverage: str
    sandboxable: str
    inclusion: PopulationInclusion


class Corpus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["corpus/top20/v0"]
    source_file: str
    research_date: str
    entries: list[CorpusEntry] = Field(min_length=20, max_length=20)

    @model_validator(mode="after")
    def _ranks_are_exactly_one_through_twenty(self) -> Corpus:
        ranks = sorted(entry.rank for entry in self.entries)
        if ranks != list(range(1, 21)):
            raise ValueError(f"corpus ranks must be exactly 1..20 with no gaps or duplicates, got {ranks}")
        return self


def load_corpus(path: Path | str = DEFAULT_CORPUS_PATH) -> Corpus:
    """Parse and validate a corpus/top20.json-shaped file."""
    raw = json.loads(Path(path).read_text())
    return Corpus.model_validate(raw)


__all__ = ["Corpus", "CorpusEntry", "DEFAULT_CORPUS_PATH", "PopulationInclusion", "load_corpus"]
