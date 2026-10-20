"""Europe PMC REST API integration for biomedical and life-sciences literature.

Search module only (https://www.ebi.ac.uk/europepmc/webservices/rest/search).
This is distinct from the full-text/OA resolution endpoints that
oa_resolver.resolve_europe_pmc already uses: the search endpoint returns
bibliographic metadata, not full text. resultType=core is requested so
abstracts and structured author lists are included; default sort is relevance.
No API key required.

Raw API field -> PaperRecord mapping (verified against live API 2026-08):
    resultList.result[].title              -> title        (trailing space stripped)
    resultList.result[].authorList.author[].fullName -> authors (structured list preferred)
      fallback: authorString split on ', ' when authorList absent (preprints)
    resultList.result[].pubYear            -> year         (fallback: firstPublicationDate[:4]; "n.d.")
    resultList.result[].journalTitle       -> venue
    resultList.result[].abstractText       -> abstract     (absent for some AGR/PAT records)
    resultList.result[].doi                -> doi          (bare DOI; normalized defensively)
    resultList.result[].pmid               -> pmid         (only present for MED source records)
    resultList.result[].fullTextUrlList.fullTextUrl[]
        [documentStyle == "pdf"].url       -> pdf_url
    https://europepmc.org/article/{source}/{id} -> source_url   (canonical, source = MED/PMC/PPR/AGR...)
    resultList.result[].citedByCount       -> citation_count
    (constant)                             -> retrieval_source="europe_pmc", screening_status="retrieved"
    paper_id                               -> make_paper_id(doi, title, year)
Europe PMC-only fields deliberately dropped (must not leak past the adapter):
id/source beyond URL construction, pmcid, meshHeadingList, keywordList,
isOpenAccess, pubType, firstIndexDate, inEPMC/InPMC flags, citedByCountYear...
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from backend.app.models.evidence import PaperRecord, make_paper_id

logger = logging.getLogger(__name__)

EUROPE_PMC_API_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EUROPE_PMC_TIMEOUT = 12.0


def _normalize_doi(doi: str | None) -> str:
    """Canonicalize a DOI string to bare lowercase form."""
    if not doi:
        return ""
    doi = doi.strip().lower()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi)
    return doi.strip()


def _extract_authors(result: dict[str, Any]) -> list[str]:
    """Prefer the structured authorList; fall back to comma-splitting authorString."""
    authors: list[str] = []
    author_list = ((result.get("authorList") or {}).get("author")) or []
    for a in author_list:
        if isinstance(a, dict):
            name = (a.get("fullName") or "").strip()
            if name:
                authors.append(name)
    if not authors:
        author_string = (result.get("authorString") or "").strip()
        if author_string:
            authors = [n.strip() for n in author_string.split(",") if n.strip()]
    return authors


def _extract_pdf_url(full_text_url_list: Any) -> str | None:
    entries = ((full_text_url_list or {}).get("fullTextUrl")) or []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("documentStyle") == "pdf" and entry.get("url"):
            return entry["url"]
    return None


async def search_europe_pmc(
    query: str,
    limit: int = 15,
    client: httpx.AsyncClient | None = None,
) -> list[PaperRecord]:
    """Search Europe PMC for literature matching the query.

    Returns structured PaperRecord objects; failures yield [].
    """
    params: dict[str, Any] = {
        "query": query,
        "format": "json",
        "resultType": "core",  # core includes abstract + structured authors
        "pageSize": min(limit, 100),
        "page": 1,
    }

    try:
        if client is not None:
            resp = await client.get(EUROPE_PMC_API_URL, params=params)
        else:
            async with httpx.AsyncClient(timeout=EUROPE_PMC_TIMEOUT, follow_redirects=True) as own_client:
                resp = await own_client.get(EUROPE_PMC_API_URL, params=params)

        if resp.status_code != 200:
            logger.warning(
                f"Europe PMC search returned status {resp.status_code} for query: {query[:40]!r}"
            )
            return []

        data = resp.json()
        results = ((data.get("resultList") or {}).get("result")) or []
        records: list[PaperRecord] = []

        for result in results:
            if not isinstance(result, dict):
                continue

            title = (result.get("title") or "").strip()
            if not title:
                continue

            # Source is one of MED/PMC/PPR/AGR/PAT/NBK; id is the record key within it.
            src = (result.get("source") or "").strip()
            rec_id = (result.get("id") or "").strip()

            doi = _normalize_doi(result.get("doi")) or None
            pmid = (str(result["pmid"]).strip()) if result.get("pmid") else None

            year = (result.get("pubYear") or "").strip()
            if not year:
                first_pub = (result.get("firstPublicationDate") or "").strip()
                year = first_pub[:4] if len(first_pub) >= 4 else "n.d."

            abstract = re.sub(r"\s+", " ", result.get("abstractText") or "").strip()

            citation_count = int(result.get("citedByCount", 0) or 0)

            paper_id = make_paper_id(doi=doi, title=title, year=year)
            records.append(PaperRecord(
                paper_id=paper_id,
                doi=doi,
                pmid=pmid,
                title=re.sub(r"\s+", " ", title),
                authors=_extract_authors(result),
                year=year or "n.d.",
                venue=result.get("journalTitle") or None,
                abstract=abstract,
                source_url=f"https://europepmc.org/article/{src}/{rec_id}" if src and rec_id else "",
                pdf_url=_extract_pdf_url(result.get("fullTextUrlList")),
                retrieval_source="europe_pmc",
                citation_count=citation_count,
                screening_status="retrieved",
            ))

        logger.info(f"Europe PMC returned {len(records)} papers for: {query[:40]!r}")
        return records

    except Exception as e:  # noqa: BLE001
        logger.warning(f"Europe PMC search failed for {query[:40]!r}: {e}")
        return []
