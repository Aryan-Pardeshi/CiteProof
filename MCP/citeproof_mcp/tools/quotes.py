# SPDX-License-Identifier: MIT
# ruff: noqa: PLE2515  (the zero-width characters in _ZERO_WIDTH_RE are the characters being removed)
"""Quote and number checks against agent-supplied source text.

``check_quote`` answers one question: does this quote appear in this text? Both
sides are normalised first (Unicode NFKC, zero-width characters removed, curly
quotes and dash variants unified, line-break hyphenation joined, hyphens inside
words removed so "long-term" matches "longterm", whitespace collapsed, lower
case). The quote must then match on word boundaries, so "ed significant" does
not match inside "showed significant" (boundaries apply to Latin, Greek and
Cyrillic letters and digits; scripts written without spaces match as substrings).

Ellipsis quotes ("a ... b", "a [...] b") verify only when every part is found,
in order, with at most ``ELLIPSIS_MAX_GAP`` normalised characters between
consecutive parts. No part is skipped: dropping a short part such as "not"
could invert the meaning of the quote.
"""

import re
import unicodedata

MIN_QUOTE_CHARS = 12
ELLIPSIS_MAX_GAP = 1500

_ZERO_WIDTH_RE = re.compile(r"[​‌‍⁠﻿]")
_SINGLE_QUOTES_RE = re.compile(r"[‘’‚‛′‵]")
_DOUBLE_QUOTES_RE = re.compile(r"[“”„‟«»″‶]")
_DASHES_RE = re.compile(r"[‐‑‒–—―−﹣－]")
_SOFT_HYPHEN_BREAK_RE = re.compile(r"­[ \t]*\r?\n\s*")
_HYPHEN_BREAK_RE = re.compile(r"-[ \t]*\r?\n\s*")
_WORD_HYPHEN_RE = re.compile(r"(?<=[^\W\d_])-(?=[^\W\d_])")
_ELLIPSIS_RE = re.compile(r"\[\s*(?:\.\s*){3,}\]|\(\s*(?:\.\s*){3,}\)|(?:\.\s*){2,}\.")


def normalize_text(text: str) -> str:
    """Normalise text for quote matching and claim keys (see module docstring)."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKC", text)
    norm = _ZERO_WIDTH_RE.sub("", norm)
    norm = _SINGLE_QUOTES_RE.sub("'", norm)
    norm = _DOUBLE_QUOTES_RE.sub('"', norm)
    norm = _DASHES_RE.sub("-", norm)
    norm = _SOFT_HYPHEN_BREAK_RE.sub("", norm)
    norm = _HYPHEN_BREAK_RE.sub("-", norm)
    norm = norm.replace("­", "")
    norm = _WORD_HYPHEN_RE.sub("", norm)
    norm = re.sub(r"\s+", " ", norm)
    return norm.lower().strip()


# Letters of scripts that separate words with spaces (Latin, Greek, Cyrillic) and digits.
# Word boundaries are enforced only for these: Chinese or Japanese text has no spaces,
# so a boundary rule there would reject every quote.
_SPACED = "0-9a-zÀ-ɏͰ-ϿЀ-ӿ"


def _bounded(part: str) -> "re.Pattern[str]":
    left = rf"(?<![{_SPACED}])" if re.match(rf"[{_SPACED}]", part) else ""
    right = rf"(?![{_SPACED}])" if re.search(rf"[{_SPACED}]$", part) else ""
    return re.compile(left + re.escape(part) + right)


def _find_in_order(parts: list[str], source: str) -> bool:
    patterns = [_bounded(p) for p in parts]
    for first in list(patterns[0].finditer(source))[:50]:
        pos = first.end()
        ok = True
        for pat in patterns[1:]:
            m = pat.search(source, pos)
            if m is None or m.start() - pos > ELLIPSIS_MAX_GAP:
                ok = False
                break
            pos = m.end()
        if ok:
            return True
    return False


def check_quote(quote: str, source_text: str) -> tuple[bool, str | None]:
    """Return ``(quote_verified, quote_note)``. The note explains a failure, or is None."""
    if not isinstance(quote, str) or not quote.strip():
        return False, "quote is empty"
    norm_quote = normalize_text(quote)
    if not norm_quote:
        return False, "quote is empty"
    norm_source = normalize_text(source_text) if isinstance(source_text, str) else ""

    if _ELLIPSIS_RE.search(norm_quote):
        parts = [p.strip(" []()\"'") for p in _ELLIPSIS_RE.split(norm_quote)]
        parts = [p for p in parts if p]
        if not parts or max(len(p) for p in parts) < MIN_QUOTE_CHARS:
            return False, f"no part of the ellipsis quote has {MIN_QUOTE_CHARS} or more normalised characters"
        if not norm_source:
            return False, "source_text is empty, so the quote could not be checked"
        if _find_in_order(parts, norm_source):
            return True, None
        return False, (
            "ellipsis quote not found: every part must appear in the supplied source text, "
            f"in order, at most {ELLIPSIS_MAX_GAP} characters apart"
        )

    if len(norm_quote) < MIN_QUOTE_CHARS:
        return False, f"quote is shorter than {MIN_QUOTE_CHARS} normalised characters and cannot be verified"
    if not norm_source:
        return False, "source_text is empty, so the quote could not be checked"
    if _bounded(norm_quote).search(norm_source):
        return True, None
    return False, "quote not found in the supplied source text"


def quote_in_source(quote: str, source_text: str) -> bool:
    """True when :func:`check_quote` verifies the quote."""
    verified, _ = check_quote(quote, source_text)
    return verified


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------

_NUMBER_TOKEN_RE = re.compile(r"\d+(?:[.,·]\d+)*")


def extract_numbers(text: str) -> set[float]:
    """Every number written in ``text``, with sign.

    '1,234' is read both as 1234 and (decimal comma) 1.234 when that is possible;
    '0·82' (Lancet style) is 0.82. A '-' counts as a minus sign only when it does
    not follow a digit, letter or closing bracket, so the range '0.70-0.96' gives
    0.70 and 0.96, while '(-0.5 to -0.1)' gives -0.5 and -0.1.
    """
    if not text:
        return set()
    norm = unicodedata.normalize("NFKC", text)
    norm = _DASHES_RE.sub("-", norm).replace("·", ".")
    found: set[float] = set()
    for m in _NUMBER_TOKEN_RE.finditer(norm):
        token = m.group(0)
        start = m.start()
        prev = norm[start - 1] if start > 0 else ""
        if prev.isalnum() or prev == "_":
            continue  # part of a word such as 'COVID19'
        if prev == ".":
            token = "0." + token  # '.5' written without the leading zero
            start -= 1
        negative = (
            start > 0
            and norm[start - 1] == "-"
            and (start == 1 or not (norm[start - 2].isalnum() or norm[start - 2] in ")]%"))
        )
        candidates: list[float] = []
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", token):
            candidates.append(float(token.replace(",", "")))
        if token.count(",") == 1 and "." not in token:
            candidates.append(float(token.replace(",", ".")))
        if "," not in token:
            try:
                candidates.append(float(token))
            except ValueError:
                pass
        for value in candidates:
            found.add(-value if negative else value)
    return found


def numbers_missing(values: dict[str, float | None], text: str) -> list[str]:
    """Names of the given values that are not written anywhere in ``text``."""
    present = extract_numbers(text)
    missing: list[str] = []
    for name, value in values.items():
        if value is None:
            continue
        if not any(abs(value - p) <= 1e-9 * max(1.0, abs(value)) for p in present):
            missing.append(name)
    return missing
