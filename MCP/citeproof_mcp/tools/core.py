# SPDX-License-Identifier: MIT
"""Core tools: reviews, DOI checks, citation resolution and prose lint.

Table owned here: ``doi_checks`` (results of verify_dois calls made with a review_id).
The backend modules are imported lazily so the server starts fast and tests can
replace the network call.
"""

import re
import sqlite3
from typing import Annotated, Any

from pydantic import Field

from citeproof_mcp import store
from citeproof_mcp.compat import MCPServer
from citeproof_mcp.tools.common import (
    MAX_TEXT_CHARS,
    cap,
    fail,
    int_arg,
    safe_tool,
    size_arg,
)

store.register_schema(
    (
        """CREATE TABLE IF NOT EXISTS doi_checks (
            doi TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            title TEXT,
            year INTEGER,
            journal TEXT,
            retracted INTEGER,
            registry TEXT,
            checked_at TEXT NOT NULL
        )""",
    )
)

MAX_DOIS = 100
MAX_PAPERS = 2000

ReviewIdOpt = Annotated[
    str | None,
    Field(description="If given, the results are saved to this review so review_status and export_bundle can report them."),
]


async def _verify_dois_lib(dois: list[str]) -> dict[str, Any]:
    from citeproof_mcp._vendor import doi_verify

    return await doi_verify.verify_dois(dois)


def _resolve_citations_lib(text: str, paper_records: list[Any]) -> Any:
    from citeproof_mcp._vendor import validation

    return validation.resolve_citations(text, paper_records)


def _lint_prose_lib(text: str) -> Any:
    from citeproof_mcp._vendor import prose_lint

    return prose_lint.lint_prose(text)


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def doi_check_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT doi, status, title, year, journal, retracted, registry, checked_at FROM doi_checks ORDER BY doi"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["retracted"] = None if d["retracted"] is None else bool(d["retracted"])
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Citation form normalisation (before the backend resolver sees the text)
# ---------------------------------------------------------------------------

_YEAR = r"(?:19|20)\d{2}"
_NOT_AUTHORS = {
    "in", "during", "since", "from", "by", "until", "before", "after", "of", "the", "a", "an", "and", "or",
    "year", "years", "published", "updated", "accessed", "january", "february", "march", "april", "may",
    "june", "july", "august", "september", "october", "november", "december", "spring", "summer",
    "autumn", "fall", "winter", "up", "through", "between", "as", "at", "on", "to",
}


def _looks_like_authors(auth: str) -> bool:
    words = auth.split()
    return bool(words) and auth[0].isupper() and len(words) <= 8 and words[0].lower().strip(",.") not in _NOT_AUTHORS
_NARRATIVE_RE = re.compile(
    r"(?P<auth>\b[^\W\d_][\w'’\-]+(?:\s+et\s+al\.?|\s+(?:and|&)\s+[^\W\d_][\w'’\-]+)?)"
    r"\s+\((?P<year>" + _YEAR + r")[a-z]?\)"
)
_GROUP_RE = re.compile(r"\(([^()]{1,400})\)")
_PART_RE = re.compile(
    r"^(?P<auth>[^\W\d_][^\d;()]*?)[,\s]+(?P<years>" + _YEAR + r"[a-z]?(?:\s*,\s*" + _YEAR + r"[a-z]?)*)"
    r"(?:\s*,\s*(?:p|pp)\.?\s*\d+(?:\s*[-–]\s*\d+)?)?$"
)
_PREFIX_RE = re.compile(r"^(?:e\.g\.,?|i\.e\.,?|see also|see|cf\.|for example,?|for a review,? see)\s+", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"\[\s*\d+(?:\s*[,–\-]\s*\d+)*\s*\]")


def _rewrite_group(match: "re.Match[str]") -> str:
    content = match.group(1)
    if not re.search(r"\b" + _YEAR, content) or re.search(r"10\.\d{4,9}/|https?://|doi:", content, re.IGNORECASE):
        return match.group(0)
    out: list[str] = []
    for part in content.split(";"):
        part = _PREFIX_RE.sub("", part.strip())
        m = _PART_RE.match(part)
        if not m:
            return match.group(0)
        auth = m.group("auth").strip().rstrip(",")
        if not _looks_like_authors(auth):
            return match.group(0)
        for year in re.findall(_YEAR, m.group("years")):
            out.append(f"({auth}, {year})")
    return " ".join(out)


def _rewrite_narrative(match: "re.Match[str]") -> str:
    auth = match.group("auth")
    if not _looks_like_authors(auth):
        return match.group(0)
    return f"({auth}, {match.group('year')})"


def normalise_citations(text: str) -> str:
    """Rewrite common author-year forms into '(Author, 2020)' so the resolver sees each one.

    Handles 'Smith et al. (2020)', '(Smith 2020)', '(Smith, 2020a)', '(Smith, 2019, 2020)',
    '(e.g., Smith, 2020; Jones and Lee, 2021)' and page suffixes. Groups containing
    DOIs or URLs are left alone.
    """
    text = _NARRATIVE_RE.sub(_rewrite_narrative, text)
    return _GROUP_RE.sub(_rewrite_group, text)


def _paper_record(p: dict[str, Any]) -> Any:
    from citeproof_mcp._vendor.evidence import PaperRecord, make_paper_id

    title = str(p.get("title") or "").strip()
    raw_year = p.get("year")
    if isinstance(raw_year, float) and raw_year.is_integer():
        raw_year = int(raw_year)
    year = str(raw_year).strip() if raw_year not in (None, "") else "n.d."
    doi = store.normalize_doi(str(p["doi"])) if p.get("doi") else None
    raw_authors = p.get("authors")
    if isinstance(raw_authors, str):
        authors = [a.strip() for a in raw_authors.split(";") if a.strip()]
    elif isinstance(raw_authors, (list, tuple)):
        authors = [str(a).strip() for a in raw_authors if a is not None and str(a).strip()]
    else:
        authors = []
    pid = str(p.get("paper_id") or make_paper_id(doi, title, year))
    return PaperRecord(paper_id=pid, title=title or "Untitled", authors=authors, year=year, doi=doi)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@safe_tool
def review_create(
    title: Annotated[str, Field(description="Short review title (max 500 chars).")],
    question: Annotated[str, Field(description="The review question in one sentence (max 2000 chars).")],
    year_min: Annotated[int | None, Field(description="Earliest publication year the question allows.")] = None,
    year_max: Annotated[int | None, Field(description="Latest publication year the question allows.")] = None,
) -> dict[str, Any]:
    """Start a new review and get the review_id every other review tool needs. Call this first.

    Returns: review_id, next_step.
    """
    review_id = store.create_review(title, question, year_min, year_max)
    return {
        "ok": True,
        "review_id": review_id,
        "next_step": "Record the protocol with protocol_record (PICO, eligibility criteria, databases, search strategy).",
    }


@safe_tool
def review_list(
    limit: Annotated[int, Field(description="Maximum reviews to return, 1 to 200.")] = 50,
    query: Annotated[
        str | None,
        Field(description="Optional case-insensitive substring filter over review title and question."),
    ] = None,
) -> dict[str, Any]:
    """List existing reviews, newest first, to recover a review_id.

    Returns: reviews (review_id, title, question, created_at, year_min, year_max), count.
    """
    n = int_arg(limit, "limit", min_value=1, max_value=200)
    needle = query.strip().lower() if isinstance(query, str) else ""
    if needle:
        reviews = [
            r
            for r in store.list_reviews(None)
            if needle in (r.get("title") or "").lower() or needle in (r.get("question") or "").lower()
        ][:n]
    else:
        reviews = store.list_reviews(n)
    for r in reviews:
        r["question"] = (r.get("question") or "")[:300]
    return {"ok": True, "reviews": reviews, "count": len(reviews)}


@safe_tool
async def verify_dois(
    dois: Annotated[
        list[str] | str,
        Field(description="Up to 100 DOIs, bare ('10.1234/abc') or as doi.org URLs. A single string may hold several DOIs separated by spaces, commas or new lines."),
    ],
    review_id: ReviewIdOpt = None,
) -> dict[str, Any]:
    """Check that DOIs exist in a registry (Crossref, then doi.org) and are not retracted.

    This is the only tool that uses the network. Statuses per DOI:
    'resolved' (registry has it), 'not_found' (doi.org says it does not exist:
    likely a typo or a fabricated citation), 'unverified' (the registry could not be
    reached or timed out: no verdict, retry later; never treat as fabricated),
    'invalid' (not DOI-shaped; not sent to any registry). retracted is true/false
    only when Crossref holds the record, else null.

    Returns: results (doi, input, status, title, year, journal, authors, retracted,
    registry), summary (checked, resolved, not_found, unverified, invalid, retracted),
    offline (true when no DOI could be checked), saved (true when stored in review_id),
    note.

    Does NOT check that a paper supports any claim, only that the DOI is real.
    """
    raw = re.split(r"[\s,;]+", dois) if isinstance(dois, str) else dois
    if not isinstance(raw, list):
        fail("dois must be a list of DOI strings")
    items = [d.strip() for d in raw if isinstance(d, str) and d.strip()]
    if not items:
        fail("dois must contain at least one DOI")
    if len(items) > MAX_DOIS:
        fail(f"too many DOIs: {len(items)}; the maximum is {MAX_DOIS} per call")

    ordered: list[tuple[str, str | None]] = []  # (input, normalised doi or None)
    seen: set = set()
    for item in items:
        doi = store.normalize_doi(item)
        key = doi or item.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append((item, doi))

    to_check = [doi for _, doi in ordered if doi]
    try:
        raw_results = await _verify_dois_lib(to_check) if to_check else {}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"DOI check could not run: {str(exc)[:200] or type(exc).__name__}. No DOI was judged; retry later.",
        }

    results: list[dict[str, Any]] = []
    summary = {"checked": len(ordered), "resolved": 0, "not_found": 0, "unverified": 0, "invalid": 0, "retracted": 0}
    for item, doi in ordered:
        if doi is None:
            status, rec = "invalid", {}
        else:
            rec = raw_results.get(doi) or {}
            status = {True: "resolved", False: "not_found"}.get(rec.get("ok"), "unverified")
        summary[status] += 1
        retracted = rec.get("retracted") if isinstance(rec.get("retracted"), bool) else None
        if retracted:
            summary["retracted"] += 1
        results.append(
            {
                "doi": doi,
                "input": item,
                "status": status,
                "title": rec.get("title") or None,
                "year": rec.get("year"),
                "journal": rec.get("journal") or None,
                "authors": rec.get("authors"),
                "retracted": retracted,
                "registry": rec.get("registry"),
            }
        )

    saved = False
    if review_id is not None and str(review_id).strip():
        now = store.now_iso()
        with store.review_db(review_id, write=True) as conn:
            for r in results:
                if r["doi"] is None:
                    continue
                conn.execute(
                    """INSERT INTO doi_checks (doi, status, title, year, journal, retracted, registry, checked_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(doi) DO UPDATE SET status = excluded.status, title = excluded.title,
                           year = excluded.year, journal = excluded.journal, retracted = excluded.retracted,
                           registry = excluded.registry, checked_at = excluded.checked_at""",
                    (
                        r["doi"], r["status"], r["title"],
                        r["year"] if isinstance(r["year"], int) else None,
                        r["journal"], None if r["retracted"] is None else int(r["retracted"]),
                        r["registry"], now,
                    ),
                )
        saved = True

    checkable = len(to_check)
    offline = checkable > 0 and summary["unverified"] == checkable
    if offline:
        note = "No registry could be reached: every DOI is 'unverified'. Nothing was judged; retry when online."
    elif summary["unverified"]:
        note = f"{summary['unverified']} DOI(s) could not be checked and are 'unverified', not fabricated; retry them."
    else:
        note = "Registry lookups only: a resolved DOI is real, which says nothing about whether it supports a claim."
    return {"ok": True, "results": results, "summary": summary, "offline": offline, "saved": saved, "note": note}


@safe_tool
def resolve_citations(
    text: Annotated[str, Field(description="Report text with inline citations (max 400000 chars).")],
    papers: Annotated[
        list[dict[str, Any]],
        Field(description="The reference list, up to 2000 objects: title, authors (list, or 'A; B'), year, optional doi."),
    ],
) -> dict[str, Any]:
    """Match the inline citations in a text against your reference list, and find references never cited.

    Recognises DOIs and author-year citations: '(Smith, 2020)', '(Smith 2020)',
    'Smith et al. (2020)', '(Smith and Lee, 2020; Jones, 2021)', '(Smith, 2020a)'.
    A citation resolves when its DOI, or first-author surname + year, matches one
    reference (authorless references match on the first title words + year).

    Returns: citations_found, resolved (citations matching exactly one reference),
    ambiguous (citations matching several references), unresolved (citations
    matching none: check them), orphan_papers (titles of references nothing cites),
    doi_citations_checked, numeric_citations_found ('[1]'-style citations, which
    this tool cannot resolve), truncated (a list was cut at 200), note.

    Does NOT check that a cited paper supports the sentence, and does not use the network.
    """
    body = size_arg(text, "text", max_len=MAX_TEXT_CHARS)
    if not isinstance(papers, list):
        fail("papers must be a list of reference objects")
    if len(papers) > MAX_PAPERS:
        fail(f"papers has {len(papers)} items; the limit is {MAX_PAPERS}")
    records = [_paper_record(p) for p in papers if isinstance(p, dict)]
    skipped = len(papers) - len(records)
    res = _resolve_citations_lib(normalise_citations(body), records)
    unresolved, cut_a = cap(list(res.unverified))
    ambiguous, cut_b = cap(list(res.ambiguous_citations))
    orphans, cut_c = cap(list(res.orphans))
    numeric = len(_NUMERIC_RE.findall(body))
    notes = ["Resolution means the citation matches a reference in your list; it is not a check of support."]
    if numeric:
        notes.append(f"{numeric} numeric citation(s) like [1] were found and not checked.")
    if skipped:
        notes.append(f"{skipped} item(s) in papers were not objects and were ignored.")
    return {
        "ok": True,
        "citations_found": res.total,
        "resolved": res.verified - len(res.ambiguous_citations),
        "ambiguous": ambiguous,
        "unresolved": unresolved,
        "orphan_papers": orphans,
        "doi_citations_checked": res.doi_citations_checked,
        "numeric_citations_found": numeric,
        "truncated": cut_a or cut_b or cut_c,
        "note": " ".join(notes),
    }


@safe_tool
def lint_prose(
    text: Annotated[str, Field(description="Report prose to check (max 400000 chars).")],
) -> dict[str, Any]:
    """Advisory style check of report prose with fixed rules.

    Codes: EMDASH (dense em-dash use), HONEST (hedged framing such as 'the honest
    answer'), PROCNOTE (narration about how the report was made), PARENDOI
    (unencoded parentheses in DOI links), LONGHEAD (sentence-like headings),
    FLATSTRUCT (flat heading structure).

    Returns: issues (code, note), issue_count, advisory (always true: findings never
    block anything).
    """
    body = size_arg(text, "text", max_len=MAX_TEXT_CHARS)
    res = _lint_prose_lib(body)
    issues = [{"code": i.code, "note": i.note} for i in res.issues]
    return {"ok": True, "issues": issues, "issue_count": len(issues), "advisory": True}


def register(server: MCPServer) -> None:
    for tool in (review_create, review_list, verify_dois, resolve_citations, lint_prose):
        server.tool()(tool)
