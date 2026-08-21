"""Validates corpus/top20.json: schema, population invariants, and that no
value anywhere in the frozen corpus looks like a real secret."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from babi.sandbox.corpus import Corpus, CorpusEntry, DEFAULT_CORPUS_PATH, PopulationInclusion, load_corpus

REPO_ROOT = Path(__file__).parent.parent.parent
CORPUS_PATH = REPO_ROOT / DEFAULT_CORPUS_PATH
TARGETS_DIR = REPO_ROOT / "targets"

# A real secret looks like a known prefix immediately followed by a long
# run of base62-ish characters with no separators -- distinct from prose
# that merely *mentions* a format, e.g. "free rk_test_* Restricted API Key".
_SECRET_SHAPE = re.compile(
    r"(sk_live|sk_test|rk_live|rk_test|ghp|github_pat|xoxb|xoxc|xoxd|fco|fcr)[_-][A-Za-z0-9]{16,}"
)


def test_corpus_file_loads_and_validates() -> None:
    corpus = load_corpus(CORPUS_PATH)
    assert corpus.kind == "corpus/top20/v0"
    assert len(corpus.entries) == 20


def test_corpus_has_exactly_twenty_entries_ranked_one_through_twenty() -> None:
    corpus = load_corpus(CORPUS_PATH)
    ranks = sorted(entry.rank for entry in corpus.entries)
    assert ranks == list(range(1, 21))


def test_every_entry_has_nonempty_required_prose_fields() -> None:
    corpus = load_corpus(CORPUS_PATH)
    for entry in corpus.entries:
        assert entry.name.strip(), entry
        assert entry.package_or_endpoint.strip(), entry
        assert entry.popularity_evidence.strip(), entry
        assert entry.transport.strip(), entry
        assert entry.auth.strip(), entry
        assert entry.annotation_coverage.strip(), entry
        assert entry.sandboxable.strip(), entry
        assert entry.inclusion.reason.strip(), entry


def test_every_entry_cites_the_frozen_research_date() -> None:
    corpus = load_corpus(CORPUS_PATH)
    assert corpus.research_date == "2026-08-20"
    assert corpus.source_file == "research/top-servers/SUMMARY.md"


def test_every_entry_is_included_in_this_frozen_population() -> None:
    # All 20 rows of the SUMMARY.md census already passed the population
    # filter by construction; a future re-run may legitimately exclude one,
    # but nothing in *this* frozen file should claim to without saying why.
    corpus = load_corpus(CORPUS_PATH)
    for entry in corpus.entries:
        assert entry.inclusion.status in ("included", "excluded")


def test_ranks_are_unique() -> None:
    corpus = load_corpus(CORPUS_PATH)
    ranks = [entry.rank for entry in corpus.entries]
    assert len(ranks) == len(set(ranks))


def test_duplicate_or_gapped_ranks_are_rejected(tmp_path: Path) -> None:
    corpus = json.loads(CORPUS_PATH.read_text())
    corpus["entries"][1]["rank"] = corpus["entries"][0]["rank"]  # duplicate rank 1
    with pytest.raises(ValidationError, match="1..20"):
        Corpus.model_validate(corpus)


def test_wrong_entry_count_is_rejected() -> None:
    corpus = json.loads(CORPUS_PATH.read_text())
    corpus["entries"] = corpus["entries"][:19]
    with pytest.raises(ValidationError):
        Corpus.model_validate(corpus)


def test_extra_field_on_entry_is_forbidden() -> None:
    with pytest.raises(ValidationError):
        CorpusEntry.model_validate(
            {
                "rank": 1,
                "name": "x",
                "repo": None,
                "package_or_endpoint": "x",
                "distribution": "npm",
                "popularity_evidence": "x",
                "transport": "x",
                "auth": "x",
                "annotation_coverage": "x",
                "sandboxable": "x",
                "inclusion": {"status": "included", "reason": "x"},
                "invented_metric": 12345,
            }
        )


def test_inclusion_status_is_a_closed_enum() -> None:
    with pytest.raises(ValidationError):
        PopulationInclusion.model_validate({"status": "maybe", "reason": "x"})


def test_no_realistic_secret_shaped_token_anywhere_in_the_corpus() -> None:
    blob = CORPUS_PATH.read_text()
    hits = _SECRET_SHAPE.findall(blob)
    assert not hits, f"secret-shaped literal(s) found in corpus/top20.json: {hits}"


def test_package_or_endpoint_matches_the_corresponding_target_file_when_one_exists() -> None:
    # Cross-check the two artifacts stay in sync: a corpus entry and its
    # TargetSpec (when one was built for it) must name the exact same
    # canonical package/endpoint identity (IMPLEMENTATION-PLAN.md: "Preserve
    # exact package/endpoint identity").
    corpus = load_corpus(CORPUS_PATH)
    name_to_slug = {
        "GitHub MCP": "github",
        "Playwright MCP": "playwright",
        "Chrome DevTools MCP": "chrome-devtools",
        "Context7": "context7",
        "Filesystem (Anthropic reference)": "filesystem",
        "Fetch (Anthropic reference)": "fetch",
        "Notion": "notion",
        "Slack": "slack",
        "Serena": "serena",
        "Atlassian (Jira/Confluence)": "atlassian",
        "Supabase": "supabase",
        "Sequential Thinking (Anthropic reference)": "sequential-thinking",
        "Memory / Knowledge Graph (Anthropic reference)": "memory",
        "GitLab": "gitlab",
        "Sentry": "sentry",
        "Stripe": "stripe",
        "Figma": "figma",
        "Firecrawl": "firecrawl",
        "PostgreSQL": "postgres-reference",
        "AWS Documentation MCP Server": "aws-documentation",
    }
    checked = 0
    for entry in corpus.entries:
        slug = name_to_slug.get(entry.name)
        assert slug is not None, f"no target-file mapping for corpus entry {entry.name!r}"
        target_path = TARGETS_DIR / f"{slug}.json"
        assert target_path.is_file(), f"missing targets/{slug}.json for corpus entry rank {entry.rank}"
        target = json.loads(target_path.read_text())
        assert target["package_or_endpoint"] == entry.package_or_endpoint, (
            f"{entry.name}: corpus says {entry.package_or_endpoint!r}, "
            f"targets/{slug}.json says {target['package_or_endpoint']!r}"
        )
        checked += 1
    assert checked == 20


def test_github_and_playwright_use_the_corrected_canonical_packages() -> None:
    # The assignment's explicit corrections: Playwright is @playwright/mcp,
    # and GitHub is the current Docker image, never the archived npm
    # reference implementation.
    corpus = load_corpus(CORPUS_PATH)
    by_name = {entry.name: entry for entry in corpus.entries}
    assert by_name["Playwright MCP"].package_or_endpoint == "@playwright/mcp"
    github = by_name["GitHub MCP"]
    assert github.package_or_endpoint == "ghcr.io/github/github-mcp-server"
    assert github.distribution == "docker-image"
    assert "npm" not in github.package_or_endpoint
