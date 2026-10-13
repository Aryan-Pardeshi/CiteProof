"""OpenAlex API integration for academic paper retrieval.

Uses the OpenAlex /works search endpoint with cursor-free single-page queries
(per_page <= 50). Joins the polite pool via the mailto param, reusing the same
OPENALEX_EMAIL env var that Unpaywall resolution already uses (no second env
var for the same purpose).

Raw API field -> PaperRecord mapping (verified against live API 2026-08):
    results[].doi                          -> doi          (full https://doi.org/ URL form; normalized to bare DOI)
    results[].title | display_name         -> title        (title preferred, display_name fallback)
    results[].authorships[].author.display_name -> authors (display-name strings, in order)
    results[].publication_year             -> year         (str; "n.d." when absent)
    results[].primary_location.source.display_name -> venue (journal/repository name)
    results[].abstract_inverted_index      -> abstract     (word->positions index; reconstructed to plain text)
    results[].ids.pmid                     -> pmid         ("https://pubmed.ncbi.nlm.nih.gov/N/" -> "N")
    results[].open_access.oa_url           -> pdf_url      (fallback: best_oa_location.pdf_url)
    results[].doi (URL) or primary_location.landing_page_url -> source_url
    results[].cited_by_count               -> citation_count
    (constant)                             -> retrieval_source="openalex", screening_status="retrieved"
    paper_id                               -> make_paper_id(doi, title, year)
OpenAlex-only fields deliberately dropped (must not leak past the adapter):
relevance_score, fwci, concepts/topics, is_retracted, biblio pages, grants.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

from backend.app.models.evidence import PaperRecord, make_paper_id

logger = logging.getLogger(__name__)

OPENALEX_API_URL = "https://api.openalex.org/works"
OPENALEX_TIMEOUT = 10.0

# Trimmed payload: only fields this adapter consumes. Keeps responses small and
# pins the contract so upstream additions can never silently change parsing.
_OPENALEX_SELECT = ",".join([
    "id", "doi", "title", "display_name", "publication_year",
    "authorships", "abstract_inverted_index", "primary_location",
    "open_access", "best_oa_location", "cited_by_count", "biblio", "ids",
])

_PMID_URL_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)")


def _normalize_doi(doi: Optional[str]) -> str:
    """Canonicalize OpenAlex's full-URL DOI form ('https://doi.org/10.x/y') to bare '10.x/y'."""
    if not doi:
        return ""
    doi = doi.strip().lower()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi)
    return doi.strip()


def reconstruct_abstract(inverted_index: Optional[Dict[str, List[int]]]) -> str:
    """Reconstruct plain text from OpenAlex's word -> [position] inverted index."""
    if not inverted_index or not isinstance(inverted_index, dict):
        return ""
    word_pos: List[Any] = []
    for word, positions in inverted_index.items():
        for pos in positions or []:
            word_pos.append((pos, word))
    word_pos.sort(key=lambda x: x[0])
    return " ".join(wp[1] for wp in word_pos)


def _extract_pmid(ids: Any) -> Optional[str]:
    """Pull the bare PMID out of ids.pmid ('https://pubmed.ncbi.nlm.nih.gov/1234/')."""
    if not ids or not isinstance(ids, dict):
        return None
    match = _PMID_URL_RE.search(str(ids.get("pmid") or ""))
    return match.group(1) if match else None


async def search_openalex(
    query: str,
    limit: int = 15,
    email: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> List[PaperRecord]:
    """Search OpenAlex works for academic literature matching the query.

    Reuses OPENALEX_EMAIL (shared with the Unpaywall resolver in
    oa_resolver.py) as the mailto polite-pool identifier; placeholder values
    are ignored. Returns structured PaperRecord objects; failures yield [].
    """
    user_email = (
        email
        or os.getenv("OPENALEX_EMAIL")
        or ""
    ).strip()

    params: Dict[str, Any] = {
        "search": query,
        "per-page": min(limit, 50),
        "select": _OPENALEX_SELECT,
    }
    if user_email and user_email != "your_email@example.com" and "@" in user_email:
        params["mailto"] = user_email

    try:
        if client is not None:
            resp = await client.get(OPENALEX_API_URL, params=params)
        else:
            async with httpx.AsyncClient(timeout=OPENALEX_TIMEOUT, follow_redirects=True) as own_client:
                resp = await own_client.get(OPENALEX_API_URL, params=params)

        if resp.status_code != 200:
            logger.warning(
                f"OpenAlex search returned status {resp.status_code} for query: {query[:40]!r}"
            )
            return []

        data = resp.json()
        items = data.get("results", [])
        records: List[PaperRecord] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            title = (item.get("title") or item.get("display_name") or "").strip()
            if not title:
                continue

            doi = _normalize_doi(item.get("doi")) or None

            authors = []
            for auth in item.get("authorships") or []:
                if isinstance(auth, dict):
                    auth_obj = auth.get("author") or {}
                    name = (auth_obj.get("display_name") or "").strip()
                    if name:
                        authors.append(name)

            year = str(item.get("publication_year") or "n.d.")

            primary_location = item.get("primary_location") or {}
            venue = ((primary_location.get("source") or {}).get("display_name")) or None

            abstract = reconstruct_abstract(item.get("abstract_inverted_index"))

            oa_info = item.get("open_access") or {}
            best_oa = item.get("best_oa_location") or {}
            pdf_url = oa_info.get("oa_url") or best_oa.get("pdf_url") or None

            landing_page = item.get("doi") or primary_location.get("landing_page_url") or ""

            citation_count = int(item.get("cited_by_count", 0) or 0)

            paper_id = make_paper_id(doi=doi, title=title, year=year)
            records.append(PaperRecord(
                paper_id=paper_id,
                doi=doi,
                pmid=_extract_pmid(item.get("ids")),
                title=title,
                authors=authors,
                year=year,
                venue=venue,
                abstract=abstract.strip(),
                source_url=landing_page,
                pdf_url=pdf_url or None,
                retrieval_source="openalex",
                citation_count=citation_count,
                screening_status="retrieved",
            ))

        logger.info(f"OpenAlex returned {len(records)} papers for: {query[:40]!r}")
        return records

    except Exception as e:
        logger.warning(f"OpenAlex search failed for {query[:40]!r}: {e}")
        return []
