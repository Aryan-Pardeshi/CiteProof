"""Semantic Scholar Graph API integration for academic paper retrieval.

Discovery/ranking source. The public (unauthenticated) tier of the Graph API
is aggressively rate limited (observed live: immediate HTTP 429 on the shared
pool); an API key is not strictly required but raises throughput
significantly. Set SEMANTIC_SCHOLAR_API_KEY to use it via the x-api-key
header. This adapter retries 429s with backoff before giving up.

Raw API field -> PaperRecord mapping (verified against live API docs 2026-08):
    data[].title                       -> title
    data[].abstract                    -> abstract
    data[].authors[].name              -> authors
    data[].year                        -> year         (str; "n.d." when absent)
    data[].venue                       -> venue
    data[].externalIds.DOI             -> doi          (normalized to bare DOI)
    data[].externalIds.PubMed          -> pmid
    data[].externalIds.ArXiv           -> arxiv_id
    data[].openAccessPdf.url           -> pdf_url
    data[].citationCount               -> citation_count
    https://doi.org/{doi} or https://www.semanticscholar.org/paper/{paperId}
                                       -> source_url
    (constant)                         -> retrieval_source="semantic_scholar", screening_status="retrieved"
    paper_id                           -> make_paper_id(doi, title, year)
S2-only fields deliberately dropped (must not leak past the adapter):
paperId, corpusId, publicationTypes, isRetracted, isOpenAccess, fieldsOfStudy.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

from backend.app.models.evidence import PaperRecord, make_paper_id

logger = logging.getLogger(__name__)

SEMANTIC_SCHOLAR_API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
SEMANTIC_SCHOLAR_TIMEOUT = 10.0

# Trimmed payload; subfields are requested explicitly so author objects always
# carry 'name' regardless of API defaults.
_S2_FIELDS = ",".join([
    "paperId", "title", "abstract", "authors.name", "year", "venue",
    "publicationDate", "citationCount", "externalIds", "openAccessPdf", "url",
])

# 429 retry policy for the shared unauthenticated pool.
_MAX_429_RETRIES = 2
_429_BACKOFF_SECONDS = (1.0, 3.0)


def _normalize_doi(doi: Optional[str]) -> str:
    """Canonicalize a DOI string to bare lowercase form."""
    if not doi:
        return ""
    doi = doi.strip().lower()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi)
    return doi.strip()


async def search_semantic_scholar(
    query: str,
    limit: int = 15,
    client: Optional[httpx.AsyncClient] = None,
) -> List[PaperRecord]:
    """Search Semantic Scholar for academic literature matching the query.

    Uses SEMANTIC_SCHOLAR_API_KEY when set. Retries on HTTP 429 with backoff;
    returns [] on persistent failure so callers' fan-out is never crashed.
    """
    headers: Dict[str, Any] = {}
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key

    params: Dict[str, Any] = {
        "query": query,
        "limit": min(limit, 50),
        "fields": _S2_FIELDS,
    }

    try:
        if client is not None:
            resp = await _get_with_retry(client, params, headers)
        else:
            async with httpx.AsyncClient(timeout=SEMANTIC_SCHOLAR_TIMEOUT, follow_redirects=True) as own_client:
                resp = await _get_with_retry(own_client, params, headers)

        if resp is None or resp.status_code != 200:
            status = resp.status_code if resp is not None else "no-response"
            logger.warning(
                f"Semantic Scholar search failed (status {status}) for query: {query[:40]!r}"
            )
            return []

        data = resp.json()
        items = data.get("data", [])
        records: List[PaperRecord] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            title = (item.get("title") or "").strip()
            if not title:
                continue

            abstract = item.get("abstract") or ""
            authors = [
                (a.get("name") or "").strip()
                for a in item.get("authors", [])
                if isinstance(a, dict) and a.get("name")
            ]
            year = str(item.get("year") or "n.d.")
            venue = item.get("venue") or None

            ext_ids = item.get("externalIds") or {}
            doi = _normalize_doi(ext_ids.get("DOI")) or None
            pmid = str(ext_ids["PubMed"]) if ext_ids.get("PubMed") else None
            arxiv_id = str(ext_ids["ArXiv"]) if ext_ids.get("ArXiv") else None

            pdf_url = (item.get("openAccessPdf") or {}).get("url")
            source_url = (
                f"https://doi.org/{doi}" if doi
                else item.get("url")
                or (f"https://www.semanticscholar.org/paper/{item.get('paperId')}" if item.get("paperId") else "")
            )

            citation_count = int(item.get("citationCount", 0) or 0)

            paper_id = make_paper_id(doi=doi, title=title, year=year)
            records.append(PaperRecord(
                paper_id=paper_id,
                doi=doi,
                pmid=pmid,
                arxiv_id=arxiv_id,
                title=title,
                authors=authors,
                year=year,
                venue=venue,
                abstract=abstract.strip(),
                source_url=source_url,
                pdf_url=pdf_url,
                retrieval_source="semantic_scholar",
                citation_count=citation_count,
                screening_status="retrieved",
            ))

        logger.info(f"Semantic Scholar returned {len(records)} papers for: {query[:40]!r}")
        return records

    except Exception as e:
        logger.warning(f"Semantic Scholar search failed for {query[:40]!r}: {e}")
        return []


async def _get_with_retry(
    client: httpx.AsyncClient,
    params: Dict[str, Any],
    headers: Dict[str, Any],
) -> Optional[httpx.Response]:
    """Issue the search GET, retrying 429 responses with exponential backoff."""
    resp: Optional[httpx.Response] = None
    for attempt in range(_MAX_429_RETRIES + 1):
        resp = await client.get(SEMANTIC_SCHOLAR_API_URL, params=params, headers=headers)
        if resp.status_code != 429:
            return resp
        if attempt < _MAX_429_RETRIES:
            # Honor Retry-After when the server sends one, else fixed backoff.
            retry_after = resp.headers.get("Retry-After")
            delay: Optional[float] = None
            if retry_after:
                try:
                    delay = float(retry_after)
                except ValueError:
                    delay = None
            await asyncio.sleep(delay if delay is not None else _429_BACKOFF_SECONDS[min(attempt, len(_429_BACKOFF_SECONDS) - 1)])
    return resp
