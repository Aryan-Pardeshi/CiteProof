# SPDX-License-Identifier: Apache-2.0
# Adapted from kernel.py of the "literature-review" skill in Anthropic's Claude Science app,
# licensed under the Apache License, Version 2.0 (see LICENSES/Apache-2.0.txt).
# Modified for CiteProof: rewritten as async httpx functions, host SDK and OpenAlex parts removed,
# User-Agent changed.
"""DOI resolution and retraction verification against Crossref and doi.org.

`verify_dois` answers two questions per DOI: does it resolve (so a citation is
not fabricated), and is it retracted (so a paper is not synthesised from a
withdrawn result). Crossref is authoritative for the retraction signal; a
doi.org HEAD is the fallback for DataCite/mEDRA/arXiv DOIs Crossref does not hold.

Every entry is a dict; network trouble is reported as `ok=None` ("could not be
verified") and never raised, so a registry outage can never be mistaken for a
fabricated DOI.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any
from urllib.parse import quote, unquote

import httpx

logger = logging.getLogger(__name__)

DOI_PATTERN = r"10\.\d{4,9}/[^\s\"'`\]\}—–&|]+"

CROSSREF_WORKS_URL = "https://api.crossref.org/works"
DOI_RESOLVER_URL = "https://doi.org/"
DOI_VERIFY_TIMEOUT = 15.0
# Crossref's polite pool asks for paced requests; at most this many DOIs are in
# flight at once so a large corpus never bursts a single registry.
DOI_VERIFY_CONCURRENCY = 4
CROSSREF_PACING_SECONDS = 0.06
RETRY_AFTER_429_SECONDS = 2.0

_BASE_USER_AGENT = "CiteProof-MCP (+https://github.com/Aryan-Pardeshi/CiteProof)"
# Config-file placeholders are not a real contact address, so they never ride along
# in the User-Agent.
_PLACEHOLDER_EMAILS = {"you@example.com", "your_email@example.com"}
# Characters that would break the User-Agent header or make it unparseable.
_UNSAFE_CONTACT_RE = re.compile(r"[\x00-\x20\x7f()\\]")


async def _sleep(seconds: float) -> None:
    """Indirection over asyncio.sleep so tests can patch out retry/pacing waits."""
    await asyncio.sleep(seconds)


def doi_user_agent(contact_email: str | None = None) -> str:
    """Product User-Agent, with `contact_email` added when the header can carry it."""
    contact = (contact_email or "").strip()
    if (
        contact
        and contact.lower() not in _PLACEHOLDER_EMAILS
        and contact.isascii()
        and not _UNSAFE_CONTACT_RE.search(contact)
    ):
        return f"{_BASE_USER_AGENT[:-1]}; mailto:{contact})"
    return _BASE_USER_AGENT


def _contact_email(contact_email: str | None) -> str | None:
    """Explicit argument wins; otherwise the shared OPENALEX_EMAIL contact."""
    return os.getenv("OPENALEX_EMAIL") if contact_email is None else contact_email


def quote_doi_path(doi: str) -> str:
    """URL-encode a DOI path; unquote each segment first so a pre-encoded
    %28 stays single-encoded (caller may pass either form)."""
    return "/".join(quote(unquote(seg), safe="") for seg in doi.split("/"))


def has_dot_segment(doi: str) -> bool:
    """True when the DOI carries an empty, '.' or '..' path segment.

    Rejected because a dot segment would resolve to a different record than the
    one that was asked for, quietly turning a wrong DOI into a valid answer. The
    registry prefix (`10.xxxx`) is exempt; every later segment must be real.
    """
    segs = unquote(doi).split("/")
    return any(seg in ("", ".", "..") for seg in segs[1:])


def _first(value: Any, default: str = "") -> str:
    """First element of a Crossref title-style list, or the scalar itself."""
    if isinstance(value, list):
        return str(value[0]) if value else default
    if isinstance(value, str):
        return value
    return default


def crossref_year(m: dict[str, Any]) -> int | None:
    """Safely extract the publication year from a Crossref `message` record."""
    dp = (m.get("published") or {}).get("date-parts") or [[None]]
    return (dp[0] or [None])[0]


def short_authors(names: list[str]) -> str | None:
    """Collapse an author list to note form: first three names,
    semicolon-separated (names may carry internal commas), then 'et al.'
    when more authors exist or any entry is nameless. Returns None when the
    record carries no author names at all.

    Authors ride in every helper's default output so that working notes built
    from them keep the name next to the DOI and year — an `(Author Year)`
    citation written from an authorless note gets its names from parametric
    memory, which supplies plausible names, not the paper's."""
    kept = [n.strip() for n in names if n and n.strip()]
    if not kept:
        return None
    more = len(names) > 3 or len(kept) < len(names)
    return "; ".join(kept[:3]) + (" et al." if more else "")


def crossref_authors(m: dict[str, Any]) -> str | None:
    """Note-form author names (family names) from a Crossref `message` record."""
    return short_authors(
        [a.get("family") or a.get("name") or "" for a in (m.get("author") or [])]
    )


def is_retracted_crossref(message: dict[str, Any]) -> bool:
    """Crossref's own retraction signals on one `message` record.

    A work counts as retracted when the registry links it to an update of type
    "retraction" (any update type containing "retract"), when its own subtype is
    "retraction", or when its title is literally prefixed with RETRACTED.
    """
    if not isinstance(message, dict):
        return False
    update_types = [
        str(u.get("type", "")) if isinstance(u, dict) else str(u or "")
        for u in (message.get("update-to") or [])
    ]
    return (
        any("retract" in t.lower() for t in update_types)
        or str(message.get("subtype") or "").lower() == "retraction"
        or _first(message.get("title")).upper().startswith("RETRACTED")
    )


async def _get_json(
    url: str,
    *,
    client: httpx.AsyncClient,
    headers: dict[str, str],
) -> dict[str, Any] | None:
    """GET `url` and JSON-decode. One 2 s retry on HTTP 429; None on any error."""
    for attempt in (0, 1):
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 429 and attempt == 0:
                await _sleep(RETRY_AFTER_429_SECONDS)
                continue
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception as e:  # noqa: BLE001 - a registry failure must never crash a check
            logger.warning(f"CrossRef request failed: {e}")
            return None
    return None


async def _head_status(
    url: str,
    *,
    client: httpx.AsyncClient,
    headers: dict[str, str],
) -> int | None:
    """HEAD `url` WITHOUT following redirects; return the origin server's own
    status (so doi.org returns 302 for a registered DOI and 404 for an
    unregistered one — not the publisher's status). One 2 s retry on 429.
    Returns None only when no status could be obtained (connection/timeout)."""
    for attempt in (0, 1):
        try:
            resp = await client.head(url, headers=headers)
        except Exception as e:  # noqa: BLE001 - a registry failure must never crash a check
            logger.warning(f"DOI resolver HEAD failed: {e}")
            return None
        if resp.status_code == 429 and attempt == 0:
            await _sleep(RETRY_AFTER_429_SECONDS)
            continue
        return resp.status_code
    return None


async def _verify_one(
    doi: str,
    *,
    client: httpx.AsyncClient,
    headers: dict[str, str],
) -> dict[str, Any]:
    """Resolve a single DOI: Crossref first, then a doi.org HEAD fallback."""
    if has_dot_segment(doi):
        return {"ok": False, "error": "dot-segment in DOI", "retracted": None}

    enc = quote_doi_path(doi)
    j = await _get_json(f"{CROSSREF_WORKS_URL}/{enc}", client=client, headers=headers)
    await _sleep(CROSSREF_PACING_SECONDS)
    if j and "message" in j:
        m = j["message"]
        return {
            "ok": True,
            "title": _first(m.get("title")),
            "authors": crossref_authors(m),
            "year": crossref_year(m),
            "journal": _first(m.get("container-title")),
            "retracted": is_retracted_crossref(m),
            "registry": "crossref",
        }

    code = await _head_status(f"{DOI_RESOLVER_URL}{enc}", client=client, headers=headers)
    if code is not None and 200 <= code < 400:
        return {"ok": True, "registry": "non-crossref", "retracted": None}
    if code == 404:
        return {"ok": False, "retracted": None}
    return {"ok": None, "error": "unverified (network)", "retracted": None}


async def verify_dois(
    dois: list[str],
    *,
    client: httpx.AsyncClient | None = None,
    contact_email: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve each DOI against Crossref, with a doi.org HEAD fallback for
    DataCite/mEDRA/arXiv DOIs. Returns {doi: {ok, title?, authors?, year?,
    journal?, retracted?, registry?, error?}} where:
      ok=True  — resolves (CrossRef hit, or doi.org 2xx/3xx);
      ok=False — does NOT resolve (doi.org 404; likely fabricated or typo);
      ok=None  — could not be verified (network/transient/5xx); do not flag as
                 fabricated.
    `retracted` is True/False only on a CrossRef hit; None when the registry
    is non-CrossRef or the lookup was unverified. Callers must read optional
    keys with `.get()`: they are absent where the registry carried no value.

    Never raises on network trouble: an unreachable registry yields `ok=None`.
    """
    cleaned = [(d or "").strip() for d in (dois or [])]
    cleaned = [d for d in cleaned if d]
    if not cleaned:
        return {}

    headers = {"User-Agent": doi_user_agent(_contact_email(contact_email))}

    if client is not None:
        return await _verify_all(cleaned, client, headers)
    async with httpx.AsyncClient(
        timeout=DOI_VERIFY_TIMEOUT,
        # Never followed: the doi.org HEAD must report the registry's own status,
        # not the publisher's.
        follow_redirects=False,
    ) as own_client:
        return await _verify_all(cleaned, own_client, headers)


async def _verify_all(
    dois: list[str],
    client: httpx.AsyncClient,
    headers: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Run `_verify_one` over `dois` with bounded concurrency; never raise."""
    semaphore = asyncio.Semaphore(DOI_VERIFY_CONCURRENCY)

    async def guarded(doi: str) -> dict[str, Any]:
        async with semaphore:
            try:
                return await _verify_one(doi, client=client, headers=headers)
            except Exception as e:  # noqa: BLE001 - one bad DOI must not stop the batch
                logger.warning(f"DOI verification failed for {doi}: {e}")
                return {"ok": None, "error": "unverified (network)", "retracted": None}

    results = await asyncio.gather(*(guarded(d) for d in dois))
    return dict(zip(dois, results))


async def crossref_lookup(
    ref_string: str,
    *,
    client: httpx.AsyncClient | None = None,
    contact_email: str | None = None,
) -> dict[str, Any] | None:
    """Find a DOI from a free-text citation (author/title/year). Returns the
    top Crossref match as {doi, title, authors, year, score} or None. Use when you have
    a citation's details but not its DOI — this is the alternative to guessing."""
    query = (ref_string or "").strip()
    if not query:
        return None

    headers = {"User-Agent": doi_user_agent(_contact_email(contact_email))}
    url = f"{CROSSREF_WORKS_URL}?query.bibliographic={quote(query)}&rows=1"

    if client is not None:
        j = await _get_json(url, client=client, headers=headers)
    else:
        async with httpx.AsyncClient(timeout=DOI_VERIFY_TIMEOUT, follow_redirects=False) as own_client:
            j = await _get_json(url, client=own_client, headers=headers)

    items = ((j or {}).get("message") or {}).get("items") or []
    if not items:
        return None
    m = items[0]
    return {
        "doi": m.get("DOI"),
        "title": _first(m.get("title")),
        "authors": crossref_authors(m),
        "year": crossref_year(m),
        "score": m.get("score"),
    }


def html_decode(s: str) -> str:
    """Minimal HTML entity decode for DOI extraction (lt/gt/amp/nbsp/slash)."""
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"), ("&nbsp;", " "), ("&#x2F;", "/"), ("&#47;", "/")):
        s = s.replace(a, b)
    return s


def extract_dois(text: str) -> list[str]:
    """Pull every DOI-looking string from `text` (for feeding to verify_dois).
    HTML-decoded, balanced-paren SICI, `</`-truncated, markdown/punct-stripped."""
    decoded = html_decode(text)
    out: set[str] = set()
    for m in re.findall(DOI_PATTERN, decoded):
        d = m.split("</")[0]
        if d.count("<") != d.count(">"):
            d = d.split("<")[0]
        # Trailing markdown emphasis and punctuation. rstrip does what the former regex did
        # (the pair alternatives `**` and `__` only repeated single characters from this set)
        # without any backtracking on long runs of `*`.
        d = d.rstrip("_]*>`,;:")
        d = d.removesuffix(".")
        while d.endswith(")") and d.count("(") < d.count(")"):
            d = d[:-1]
        if len(d) > 8:
            out.add(d)
    return sorted(out)
