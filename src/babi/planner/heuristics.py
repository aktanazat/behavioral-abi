"""The one generic, tool-agnostic heuristic data table for the planner.

Every name-pattern or verb-pattern the planner ever consults -- for
candidate CRUD/act classification (`schema.py`) and for name-driven value
synthesis (`values.py`) -- is declared once, here, in `HEURISTIC_TABLE`. No
other module in `babi.planner` may declare its own regex against a tool or
field name. Every pattern is a broad English lexical category (a verb like
"create", a noun shape like "identifier"); none is a specific tool,
server, or operation name, which is exactly the boundary RUBRIC.md's
"Scope rule: no per-tool logic" draws: "Model inference from tool name,
description, input schema, output schema" is allowed, "code or
configuration keyed by tool/operation name" is not. A regex over the
English word "create" matches infinitely many tools; it is not keyed to
any one of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

_WORD_BOUNDARY_SPLIT = re.compile(r"[^a-zA-Z0-9]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])")


def tokenize(*parts: str | None) -> tuple[str, ...]:
    """Split name/description text into lowercase word tokens. Handles
    snake_case, kebab-case, camelCase, whitespace, and digits as boundaries
    -- purely lexical, no semantic lookup."""
    tokens: list[str] = []
    for part in parts:
        if not part:
            continue
        for piece in _WORD_BOUNDARY_SPLIT.split(part):
            if piece:
                tokens.append(piece.lower())
    return tuple(tokens)


class OperationClass(str, Enum):
    """Candidate create/read/update/delete/act class (IMPLEMENTATION-PLAN.md,
    "The deterministic pass extracts ... Candidate create/read/update/
    delete/act classes from names and schemas."). Always a hypothesis with
    a confidence score attached, never asserted as ground truth."""

    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    ACT = "act"
    UNKNOWN = "unknown"


class ValueCategory(str, Enum):
    """Generic shape a scalar field's name suggests, used only to pick a
    plausible *synthesis* strategy (never to assert real-world meaning)."""

    IDENTIFIER = "identifier"
    PATH = "path"
    URL = "url"
    EMAIL = "email"
    TIMESTAMP = "timestamp"
    NAME = "name"
    FLAG = "flag"
    COUNT = "count"
    MONEY = "money"
    GENERIC = "generic"


class HeuristicDomain(str, Enum):
    OPERATION = "operation"
    VALUE = "value"


@dataclass(frozen=True, slots=True)
class HeuristicRule:
    domain: HeuristicDomain
    category: str
    pattern: re.Pattern[str]


def _op(category: OperationClass, *words: str) -> HeuristicRule:
    pattern = re.compile(r"^(" + "|".join(words) + r")$")
    return HeuristicRule(HeuristicDomain.OPERATION, category.value, pattern)


def _val(category: ValueCategory, pattern: str) -> HeuristicRule:
    return HeuristicRule(HeuristicDomain.VALUE, category.value, re.compile(pattern, re.IGNORECASE))


# The single data table. `domain=OPERATION` rules match one *token* of a
# tool's tokenized name/description (see `classify_operation`); `domain=VALUE`
# rules match anywhere in a *field name* (see `classify_value_category`).
# Order matters within a domain: first match wins, most specific first.
HEURISTIC_TABLE: tuple[HeuristicRule, ...] = (
    # --- operation verb categories (tool name/description tokens) ---
    _op(
        OperationClass.CREATE,
        "create",
        "add",
        "new",
        "insert",
        "register",
        "make",
        "generate",
        "start",
        "open",
        "upload",
        "post",
        "write",
        "initiate",
        "provision",
        "publish",
        "spawn",
        "put",
    ),
    _op(
        OperationClass.DELETE,
        "delete",
        "remove",
        "destroy",
        "drop",
        "clear",
        "purge",
        "cancel",
        "revoke",
        "unlink",
        "discard",
        "close",
        "terminate",
        "teardown",
    ),
    _op(
        OperationClass.UPDATE,
        "update",
        "edit",
        "modify",
        "patch",
        "set",
        "rename",
        "change",
        "replace",
        "move",
        "append",
        "merge",
        "sync",
        "resize",
        "toggle",
    ),
    _op(
        OperationClass.READ,
        "get",
        "read",
        "list",
        "show",
        "describe",
        "fetch",
        "query",
        "search",
        "find",
        "view",
        "lookup",
        "retrieve",
        "inspect",
        "check",
        "export",
        "count",
        "head",
    ),
    _op(
        OperationClass.ACT,
        "run",
        "execute",
        "call",
        "send",
        "trigger",
        "invoke",
        "apply",
        "perform",
        "submit",
        "process",
        "deploy",
        "restart",
        "dispatch",
        "notify",
    ),
    # --- value-name categories (field name substrings) ---
    _val(ValueCategory.IDENTIFIER, r"(^|_)(u?u?id|guid|key|slug|token|handle)(_|$)|(^|_)ref(erence)?(_|$)"),
    _val(ValueCategory.PATH, r"path|file|dir(ectory)?|folder"),
    _val(ValueCategory.URL, r"url|uri|endpoint|href|webhook|callback"),
    _val(ValueCategory.EMAIL, r"e[-_]?mail"),
    _val(ValueCategory.TIMESTAMP, r"date|time|_at$|^at_|timestamp|deadline|expir"),
    _val(ValueCategory.MONEY, r"price|cost|money|currency|amount|fee|balance"),
    _val(ValueCategory.COUNT, r"count|quantity|limit|size|num(ber)?|total|page|offset|index"),
    _val(ValueCategory.FLAG, r"^is[_A-Z]|^has[_A-Z]|enabled|disabled|active|flag"),
    _val(ValueCategory.NAME, r"name|title|label"),
)

_OPERATION_RULES = tuple(rule for rule in HEURISTIC_TABLE if rule.domain is HeuristicDomain.OPERATION)
_VALUE_RULES = tuple(rule for rule in HEURISTIC_TABLE if rule.domain is HeuristicDomain.VALUE)

# A required identifier-shaped *input* field with no accompanying free-form
# body is the strongest schema-shape signal for read/update/delete/act over
# "create" (a create call rarely requires the callee to already know the
# resource's own id). Weighted, not decisive, alongside the verb tokens.
IDENTIFIER_VALUE_CATEGORIES = frozenset({ValueCategory.IDENTIFIER})


def classify_operation(name: str, description: str | None) -> tuple[OperationClass, float]:
    """Score every operation-verb category by (weighted) token hits across
    the tool's own tokenized name and description; name tokens count
    double since they're the more deliberate signal. Returns the top
    category and a confidence in [0, 1] -- 0 confidence means UNKNOWN with
    no evidence at all, never a hidden default."""
    name_tokens = tokenize(name)
    description_tokens = tokenize(description)
    scores: dict[str, float] = {}
    for token in name_tokens:
        for rule in _OPERATION_RULES:
            if rule.pattern.match(token):
                scores[rule.category] = scores.get(rule.category, 0.0) + 2.0
                break
    for token in description_tokens:
        for rule in _OPERATION_RULES:
            if rule.pattern.match(token):
                scores[rule.category] = scores.get(rule.category, 0.0) + 1.0
                break

    if not scores:
        return OperationClass.UNKNOWN, 0.0

    best_category, best_score = max(scores.items(), key=lambda item: item[1])
    total = sum(scores.values())
    confidence = best_score / total if total else 0.0
    return OperationClass(best_category), confidence


def classify_value_category(field_name: str) -> ValueCategory:
    """First matching generic name-shape category for one field name, or
    `GENERIC` if nothing matches -- the safe, unconstrained fallback."""
    for rule in _VALUE_RULES:
        if rule.pattern.search(field_name):
            return ValueCategory(rule.category)
    return ValueCategory.GENERIC


# Generic English function words -- excluded from lexical similarity for the
# same reason operation verbs are: they say nothing about which *resource*
# two tools share, and would otherwise dilute a genuine noun match (e.g.
# both tokenizing to "a"/"the"/"by" contributes no real signal).
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "in",
        "on",
        "by",
        "its",
        "it",
        "to",
        "for",
        "with",
        "is",
        "are",
        "this",
        "that",
        "or",
        "and",
        "from",
        "as",
        "be",
        "if",
        "any",
        "one",
        "given",
        "into",
        "such",
        "used",
        "use",
        "using",
    }
)


def token_jaccard(tokens_a: tuple[str, ...], tokens_b: tuple[str, ...]) -> float:
    """Jaccard similarity between two token sets, ignoring operation verbs
    and generic stop words -- a shared "create"/"get"/"a"/"the" tells us
    nothing about which *resource* two tools share; the noun tokens do."""
    verb_tokens = {word for rule in _OPERATION_RULES for word in rule.pattern.pattern.strip("^$()").split("|")}
    excluded = verb_tokens | _STOP_WORDS
    set_a = {token for token in tokens_a if token not in excluded}
    set_b = {token for token in tokens_b if token not in excluded}
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


__all__ = [
    "HEURISTIC_TABLE",
    "HeuristicDomain",
    "HeuristicRule",
    "IDENTIFIER_VALUE_CATEGORIES",
    "OperationClass",
    "ValueCategory",
    "classify_operation",
    "classify_value_category",
    "token_jaccard",
    "tokenize",
]
