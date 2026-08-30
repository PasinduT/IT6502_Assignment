"""Deterministic query analysis used by runtime retrieval.

This module intentionally contains no model/provider calls.  Keeping query interpretation
small and deterministic makes retrieval reproducible and means a follow-up cannot inherit
facts from an assistant response.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# This is a reviewed, stable vocabulary.  Values are included in the lexical query sent to
# Search, while the keys make common IRD abbreviations searchable in either direction.
QUERY_ALIASES: dict[str, str] = {
    "APIT": "Advance Personal Income Tax",
    "AIT": "Advance Income Tax",
    "WHT": "Withholding Tax",
    "SET": "Statement of Estimated Tax Payable",
    "SSCL": "Social Security Contribution Levy",
    "TIN": "Taxpayer Identification Number",
    "RAMIS": "Revenue Administration Management Information System",
    "IIT": "Individual Income Tax",
    "CIT": "Corporate Income Tax",
    "PIT": "Partnership Income Tax",
    "VAT": "Value Added Tax",
}
# An alias named ALIAS_MAP is convenient for callers and preserves a descriptive public name.
ALIAS_MAP = QUERY_ALIASES

_PROCEDURAL_RE = re.compile(
    r"\b(?:how|where|which|what)\b.*\b(?:file|complete|register|pay|upload|submit|amend|"
    r"appeal|obtain|navigate|identify)\b|\b(?:file|complete|register|pay|upload|submit|"
    r"amend|appeal|obtain|navigate|identify)\b",
    re.IGNORECASE,
)
_HISTORICAL_RE = re.compile(
    r"\b(?:histor(?:y|ical|ically)|previous(?:ly)?|prior|old|past|earlier|last year|"
    r"superseded|former|before)\b",
    re.IGNORECASE,
)
_FOLLOWUP_RE = re.compile(
    r"\b(?:what about|how about|and|also|instead|that|those|this|it|them|same|more|"
    r"another|for (?:individuals?|partnerships?|companies?|businesses?))\b",
    re.IGNORECASE,
)

_TAX_TYPE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("individual_income_tax", ("individual income tax", "personal income tax", "iit")),
    ("corporate_income_tax", ("corporate income tax", "company income tax", "cit")),
    ("partnership_income_tax", ("partnership income tax", "partnership", "pit")),
    ("income_tax", ("income tax",)),
    ("apit", ("apit", "advance personal income tax", "advance income tax")),
    ("ait", ("ait", "advance income tax")),
    ("wht", ("wht", "withholding tax")),
    ("sscl", ("sscl", "social security contribution levy")),
    ("stamp_duty", ("stamp duty",)),
    ("vat", ("vat", "value added tax")),
)
_TAXPAYER_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("individual", ("individual", "personal taxpayer", "person")),
    ("partnership", ("partnership", "partners")),
    ("company", ("company", "companies", "corporate", "corporation")),
)


@dataclass(frozen=True, slots=True)
class QueryContext:
    tax_year: str | None
    tax_types: list[str]
    taxpayer_types: list[str]
    procedural_intent: bool
    historical_intent: bool
    retrieval_text: str


def extract_tax_year(question: str) -> str | None:
    """Return the explicit tax period in a query, preserving single-year compatibility.

    Both ``2025/26`` and ``YA 2526`` are normalized to the registry's four-digit period;
    a standalone calendar year remains ``2025`` for compatibility with the original API.
    """

    text = question or ""
    period = re.search(r"\b(20\d{2})\s*[/-]\s*(\d{2}|20\d{2})\b", text)
    if period:
        end = period.group(2)
        if len(end) == 2:
            end = period.group(1)[:2] + end
        return f"{period.group(1)}/{end}"
    ya = re.search(r"\bYA\s*['-]?\s*(\d{2})(\d{2})\b", text, re.IGNORECASE)
    if ya:
        return f"20{ya.group(1)}/20{ya.group(2)}"
    year = re.search(r"\b(20\d{2})\b", text)
    return year.group(1) if year else None


def _message_parts(messages: Sequence[Any] | None) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for message in messages or ():
        if isinstance(message, Mapping):
            role, content = message.get("role"), message.get("content")
        else:
            role, content = getattr(message, "role", None), getattr(message, "content", None)
        if isinstance(role, str) and isinstance(content, str):
            result.append((role.lower(), content.strip()))
    return [(role, content) for role, content in result if content]


def build_retrieval_text(question: str, messages: Sequence[Any] | None = None) -> str:
    """Build bounded lexical context from user messages only.

    The latest question is always present.  Up to two preceding user messages are added only
    for likely follow-ups or otherwise short/underspecified questions; assistant messages are
    deliberately ignored even when they occur between user messages.
    """

    latest = (question or "").strip()
    if not latest:
        return ""
    users = [content for role, content in _message_parts(messages) if role == "user"]
    if users and users[-1] == latest:
        preceding = users[:-1]
    else:
        preceding = users
    needs_context = len(latest.split()) < 8 or _FOLLOWUP_RE.search(latest) is not None
    selected = preceding[-2:] if needs_context else []
    return "\n".join([*selected, latest])


def expand_aliases(text: str) -> str:
    """Append deterministic long-form expansions for aliases present in ``text``."""

    expansions: list[str] = []
    for alias, expansion in QUERY_ALIASES.items():
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text, re.IGNORECASE):
            expansions.append(expansion)
    return f"{text} {' '.join(expansions)}".strip() if expansions else text


def _find_labels(text: str, patterns: tuple[tuple[str, tuple[str, ...]], ...]) -> list[str]:
    lowered = text.casefold()
    found: list[str] = []
    for label, phrases in patterns:
        if any(re.search(rf"(?<![\w]){re.escape(phrase)}(?![\w])", lowered) for phrase in phrases):
            found.append(label)
    return found


def _find_tax_types(text: str) -> list[str]:
    found = _find_labels(text, _TAX_TYPE_PATTERNS)
    # The broad ``income_tax`` label is useful only when no more specific income-tax
    # category was identified.
    if any(
        label in found
        for label in ("individual_income_tax", "corporate_income_tax", "partnership_income_tax")
    ):
        found = [label for label in found if label != "income_tax"]
    return found


def analyze_query(question: str, messages: Sequence[Any] | None = None) -> QueryContext:
    """Analyze one latest user question with bounded preceding user context."""

    retrieval_text = build_retrieval_text(question, messages)
    return QueryContext(
        tax_year=extract_tax_year(retrieval_text),
        tax_types=_find_tax_types(retrieval_text),
        taxpayer_types=_find_labels(retrieval_text, _TAXPAYER_PATTERNS),
        procedural_intent=_PROCEDURAL_RE.search(retrieval_text) is not None,
        historical_intent=_HISTORICAL_RE.search(retrieval_text) is not None,
        retrieval_text=expand_aliases(retrieval_text),
    )


# Readable aliases for orchestration and migration callers.
analyze = analyze_query
build_query_context = analyze_query
analyze_query_context = analyze_query
