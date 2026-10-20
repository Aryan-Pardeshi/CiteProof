# SPDX-License-Identifier: Apache-2.0
# Adapted from style_pass in kernel.py of the "literature-review" skill in Anthropic's Claude Science
# app, licensed under the Apache License, Version 2.0 (see LICENSES/Apache-2.0.txt).
# Modified for CiteProof: typed result, extra process-narration patterns, no model parameter.
"""Deterministic prose lint for exported reports.

Flags the failure modes that survive into a finished report: process narration
("DOIs were verified against Crossref"), hedged framing ("the honest answer"),
sentence-style headings and a flat list of top-level sections.

No LLM call by design. Drafts routinely quote retrieved third-party text, and a
free-text fix hint that a model is instructed to apply would be an indirect
injection channel. The regex codes below are the whole check, so retrieved text
can only ever add or remove a code, never inject a new instruction.

`ok` is advisory: it reports the lint result, it never blocks an export.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

EM_DASH = "—"

# An em-dash run only counts when it is both dense and numerous: more than six
# of them AND more than eight per 1000 words. A single long sentence with six
# em-dashes is normal typography, not a dash habit.
EM_DASH_MAX_COUNT = 6
EM_DASH_MAX_PER_1KW = 8.0

# Hedged framing that guards a sentence instead of making it.
_HONEST_RE = re.compile(
    r"\b(the\s+|an?\s+)?honest(ly)?\s+"
    r"(answer|summary|read|reading|look|perspective|assessment|appraisal|take|view)\b",
    re.IGNORECASE,
)

# Narration about how the report was produced rather than about the subject.
# The first four patterns are the originals; the rest catch AI self-reference
# and pipeline narration, which read the same way in an export.
_PROC_NOTE_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"DOIs?\s+(were\s+)?verif",
        r"verified against (CrossRef|PubMed)",
        r"no retraction",
        r"current as of",
        r"\bas an ai\b",
        r"\bI (have )?(searched|retrieved|reviewed)\b",
        r"\bthis (report|review) was generated\b",
        r"\bthe (pipeline|agent|system) (ran|performed|retrieved)\b",
    )
)

# A markdown link whose href carries an unencoded parenthesis: the link breaks in
# every renderer that balances parentheses in the destination.
_PAREN_DOI_RE = re.compile(r"\]\(https://doi\.org/[^)\s]*\([^)\s]*\)")

_H2_PREFIX = "## "
_H3_PREFIX = "### "
# A heading longer than this reads as a sentence rather than a noun phrase.
_LONG_HEADING_MAX_WORDS = 8
_LONG_HEADING_MIN_COUNT = 2
_FLAT_STRUCTURE_MIN_H2 = 7


@dataclass(frozen=True)
class ProseIssue:
    """One deterministic finding: a stable code and a note explaining the fix."""

    code: str
    note: str


@dataclass(frozen=True)
class ProseLintResult:
    """Outcome of `lint_prose`; `ok` is true only when no issue fired."""

    ok: bool
    issues: tuple[ProseIssue, ...]


def _find_process_notes(draft: str) -> list[str]:
    """Return each distinct process-narration phrase found, in pattern order."""
    hits: list[str] = []
    for pattern in _PROC_NOTE_PATTERNS:
        match = pattern.search(draft)
        if not match:
            continue
        phrase = match.group(0).strip()
        if phrase and phrase not in hits:
            hits.append(phrase)
    return hits


def lint_prose(text: str) -> ProseLintResult:
    """Run the deterministic prose lint over `text`.

    Returns every finding in a fixed order (EMDASH, HONEST, PROCNOTE, PARENDOI,
    LONGHEAD, FLATSTRUCT). Purely string-in/result-out: no model call, no I/O.
    """
    draft = text or ""
    issues: list[ProseIssue] = []

    words = len(draft.split()) or 1
    em_dashes = draft.count(EM_DASH)
    if em_dashes > EM_DASH_MAX_COUNT and 1000 * em_dashes / words > EM_DASH_MAX_PER_1KW:
        issues.append(
            ProseIssue(
                "EMDASH",
                f"{em_dashes} em-dashes ({1000 * em_dashes / words:.0f}/1kw); replace most "
                "with comma/colon/period, keep at most one per paragraph",
            )
        )

    honest = _HONEST_RE.search(draft)
    if honest:
        issues.append(
            ProseIssue("HONEST", f"{honest.group(0)!r}: drop the framing, write the sentence it was guarding")
        )

    proc_notes = _find_process_notes(draft)
    if proc_notes:
        issues.append(
            ProseIssue("PROCNOTE", f"process-narration line present ({'; '.join(proc_notes)}); delete it")
        )

    if _PAREN_DOI_RE.search(draft):
        issues.append(
            ProseIssue(
                "PARENDOI",
                "DOI href contains literal ( ); URL-encode as %28 %29 so the markdown link "
                "survives simpler renderers",
            )
        )

    lines = draft.split("\n")
    h2 = [ln for ln in lines if ln.startswith(_H2_PREFIX)]
    long_h2 = [ln for ln in h2 if len(ln.split()) > _LONG_HEADING_MAX_WORDS]
    if len(long_h2) >= _LONG_HEADING_MIN_COUNT:
        issues.append(
            ProseIssue("LONGHEAD", f"{len(long_h2)} headings read as sentences; shorten to <=6-word noun phrases")
        )
    if len(h2) >= _FLAT_STRUCTURE_MIN_H2 and not any(ln.startswith(_H3_PREFIX) for ln in lines):
        issues.append(
            ProseIssue(
                "FLATSTRUCT",
                f"{len(h2)} top-level sections, no subsections; group related ## under a parent "
                "and demote to ###",
            )
        )

    return ProseLintResult(ok=not issues, issues=tuple(issues))