"""Reject a finished run whose final text is not a usable report.

Free and small models occasionally answer a report prompt with a safety verdict
or a single line. A run must fail with a clear error rather than "complete" with
that output.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

# Named thresholds so the rule is easy to review and change.
MIN_REPORT_WORDS = 80
MIN_SECTION_WORDS = 30
MIN_RESEARCH_PAPER_WORDS = 20

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_RULE_RE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['\u2019-][A-Za-z0-9]+)*")
_YEAR = r"(?:19|20)\d{2}[a-z]?"
# APA in-text citations: parenthetical "(Smith et al., 2020)" or
# "(Smith, 2020; Lee & Park, 2019)", and narrative "Smith et al. (2020)".
# Each span is bounded: an unbounded scan after an unclosed "(" is quadratic
# and took seconds on a paper-length text.
_IN_TEXT_CITATION_RE = re.compile(
    rf"\((?=[^()]{{0,300}}?[A-Za-z])[^()]{{0,300}}?,\s*{_YEAR}\b[^()]{{0,300}}\)"
    rf"|\b[A-Z][A-Za-z'\u2019-]+(?:\s+et\s+al\.|\s+(?:and|&)\s+[A-Z][A-Za-z'\u2019-]+)?\s+\({_YEAR}\)"
)

_PAPER_TEXT_FIELDS = (
    "abstract",
    "introduction",
    "literature_review",
    "research_gap",
    "research_design",
    "data_collection_plan",
    "data_analysis_plan",
    "results",
    "discussion",
    "implications",
    "limitations",
    "conclusion",
    "future_scope",
)


class ReportGenerationError(RuntimeError):
    """Raised when a run finished without a usable report.

    The message is fixed and safe to show to a user; the reason is kept for logs.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(
            "The report could not be generated because the model returned "
            "incomplete or uncited output. Please try again."
        )


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _section_words(text: str) -> int:
    """Words outside headings, horizontal rules and bare source links."""
    total = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or _HEADING_RE.match(stripped) or _RULE_RE.match(stripped):
            continue
        if _URL_RE.fullmatch(stripped.lstrip("-* ").strip()):
            continue
        total += _word_count(stripped)
    return total


def _citation_urls(values: Sequence[Any]) -> list[str]:
    urls: list[str] = []
    for item in values:
        if isinstance(item, str):
            cleaned = item.strip()
        elif isinstance(item, Mapping):
            value = item.get("url")
            cleaned = value.strip() if isinstance(value, str) else ""
        else:
            cleaned = ""
        if cleaned:
            urls.append(cleaned)
    return urls


def deepsearch_report_is_valid(
    report: Any,
    *,
    citations: Sequence[Any] = (),
) -> bool:
    """Return True only for a report with body text and its retrieved sources."""
    if not isinstance(report, str):
        return False
    text = report.strip()
    if not text:
        return False
    if _word_count(text) < MIN_REPORT_WORDS:
        return False
    if _section_words(text) < MIN_SECTION_WORDS:
        return False

    retrieved = _citation_urls(citations)
    return not (retrieved and not any(url in text for url in retrieved))


def research_paper_text(values: Mapping[str, Any]) -> str:
    """Join the written sections of a Research Mode paper."""
    parts = [
        str(values.get(field) or "")
        for field in _PAPER_TEXT_FIELDS
        if values.get(field)
    ]
    return "\n\n".join(part.strip() for part in parts if part.strip())


def has_in_text_citation(text: str) -> bool:
    """True when the text carries at least one APA author-year citation."""
    return bool(_IN_TEXT_CITATION_RE.search(text or ""))


def research_paper_is_valid(values: Mapping[str, Any]) -> bool:
    """A paper needs written sections, at least one reference, and at least
    one in-text citation (a reference list the prose never cites is the
    uncited output this guard exists to reject)."""
    text = research_paper_text(values)
    if _word_count(text) < MIN_RESEARCH_PAPER_WORDS:
        return False
    references = values.get("references")
    if not (isinstance(references, (list, tuple)) and references):
        return False
    return has_in_text_citation(text)


__all__ = [
    "MIN_REPORT_WORDS",
    "MIN_RESEARCH_PAPER_WORDS",
    "MIN_SECTION_WORDS",
    "ReportGenerationError",
    "deepsearch_report_is_valid",
    "has_in_text_citation",
    "research_paper_is_valid",
    "research_paper_text",
]
