"""DOAJ (Directory of Open Access Journals) article search integration.

Uses the DOAJ API v2 *article* search endpoint only
(https://doaj.org/api/search/articles/{query}); the separate journal-search
endpoint is irrelevant here since only articles become PaperRecords.
No API key required.

Raw API field -> PaperRecord mapping (verified against live API 2026-08):
    results[].bibjson.title                -> title
    results[].bibjson.author[].name        -> authors     (affiliation dropped; PaperRecord.authors is List[str])
    results[].bibjson.year                 -> year        (string, e.g. "2025"; "n.d." when absent)
    results[].bibjson.journal.title        -> venue
    results[].bibjson.abstract             -> abstract
    results[].bibjson.identifier[] where type=="doi" -> doi   (bare DOI)
    results[].bibjson.link[] where type=="fulltext"  -> source_url (first entry; pdf_url when content_type is PDF)
    (constant)                             -> citation_count=0  (DOAJ exposes no citation counts)
    (constant)                             -> retrieval_source="doaj", screening_status="retrieved"
    paper_id                               -> make_paper_id(doi, title, year)
DOAJ-only fields deliberately dropped (must not leak past the adapter):
bibjson.identifier pissn/eissn, journal.issns/volume/number/country/publisher,
keywords, subject (LCC codes), month, last_updated timestamp.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any

import httpx

from backend.app.models.evidence import PaperRecord, make_paper_id

logger = logging.getLogger(__name__)

DOAJ_API_BASE = "https://doaj.org/api/search/articles"
DOAJ_TIMEOUT = 12.0


def _normalize_doi(doi: str | None) -> str:
    """Canonicalize a DOI string to bare lowercase form."""
    if not doi:
        return ""
    doi = doi.strip().lower()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi)
    return doi.strip()


def _extract_identifiers(bibjson: dict[str, Any]) -> dict[str, str]:
    """Flatten bibjson.identifier[] ({id, type}) into {type: id}."""
    out: dict[str, str] = {}
    for ident in bibjson.get("identifier") or []:
        if isinstance(ident, dict) and ident.get("type") and ident.get("id"):
            out[ident["type"]] = str(ident["id"]).strip()
    return out


def _extract_links(bibjson: dict[str, Any]) -> tuple:
    """Return (source_url, pdf_url) from bibjson.link[] entries."""
    source_url = ""
    pdf_url: str | None = None
    for link in bibjson.get("link") or []:
        if not isinstance(link, dict) or not link.get("url"):
            continue
        url = str(link["url"]).strip()
        if link.get("type") == "fulltext" and not source_url:
            source_url = url
            content_type = str(link.get("content_type") or "").lower()
            if "pdf" in content_type or url.lower().endswith(".pdf"):
                pdf_url = url
    return source_url, pdf_url


async def search_doaj(
    query: str,
    limit: int = 15,
    client: httpx.AsyncClient | None = None,
) -> list[PaperRecord]:
    """Search DOAJ open-access articles matching the query.

    Returns structured PaperRecord objects; failures yield [].
    """
    # The query is a path segment on the v2 article-search endpoint.
    url = f"{DOAJ_API_BASE}/{urllib.parse.quote(query, safe='')}"

    params: dict[str, Any] = {
        "pageSize": min(limit, 50),
        "page": 1,
    }

    try:
        if client is not None:
            resp = await client.get(url, params=params)
        else:
            async with httpx.AsyncClient(timeout=DOAJ_TIMEOUT, follow_redirects=True) as own_client:
                resp = await own_client.get(url, params=params)

        if resp.status_code != 200:
            logger.warning(
                f"DOAJ search returned status {resp.status_code} for query: {query[:40]!r}"
            )
            return []

        data = resp.json()
        items = data.get("results", [])
        records: list[PaperRecord] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            bibjson = item.get("bibjson")
            if not isinstance(bibjson, dict):
                continue

            title = (bibjson.get("title") or "").strip()
            if not title:
                continue

            identifiers = _extract_identifiers(bibjson)
            doi = _normalize_doi(identifiers.get("doi")) or None

            authors = [
                (a.get("name") or "").strip()
                for a in bibjson.get("author") or []
                if isinstance(a, dict) and a.get("name")
            ]

            year = str(bibjson.get("year") or "").strip() or "n.d."
            abstract = re.sub(r"\s+", " ", bibjson.get("abstract") or "").strip()
            source_url, pdf_url = _extract_links(bibjson)

            paper_id = make_paper_id(doi=doi, title=title, year=year)
            records.append(PaperRecord(
                paper_id=paper_id,
                doi=doi,
                title=re.sub(r"\s+", " ", title),
                authors=authors,
                year=year,
                venue=bibjson.get("journal", {}).get("title") if isinstance(bibjson.get("journal"), dict) else None,
                abstract=abstract,
                source_url=source_url,
                pdf_url=pdf_url,
                retrieval_source="doaj",
                citation_count=0,
                screening_status="retrieved",
            ))

        logger.info(f"DOAJ returned {len(records)} papers for: {query[:40]!r}")
        return records

    except Exception as e:  # noqa: BLE001
        logger.warning(f"DOAJ search failed for {query[:40]!r}: {e}")
        return []
