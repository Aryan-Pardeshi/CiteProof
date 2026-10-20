"""PubMed (NCBI E-utilities) integration for biomedical and life sciences literature.

Queries PubMed using esearch.fcgi, esummary.fcgi, and efetch.fcgi with rate limits.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from backend.app.models.evidence import PaperRecord, make_paper_id

logger = logging.getLogger(__name__)

NCBI_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PUBMED_TIMEOUT = 12.0

_pubmed_semaphore = asyncio.Semaphore(2)
_pubmed_schedule_lock = asyncio.Lock()
_last_pubmed_request_time = 0.0


async def _rate_limited_get(client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> httpx.Response:
    global _last_pubmed_request_time
    async with _pubmed_semaphore:
        # Serialize the elapsed-time check, sleep, and timestamp update so
        # concurrent callers cannot all observe the same "elapsed" value and
        # issue their requests in the same instant.
        async with _pubmed_schedule_lock:
            now = time.monotonic()
            elapsed = now - _last_pubmed_request_time
            min_interval = 0.35  # Max 3 req/sec unauthenticated
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)
            _last_pubmed_request_time = time.monotonic()
        return await client.get(url, params=params)


async def search_pubmed(
    query: str,
    limit: int = 15,
    email: str | None = None,
    tool: str | None = None
) -> list[PaperRecord]:
    """Search PubMed via NCBI E-utilities, populating abstracts via efetch.fcgi.

    NCBI requires a real, registered contact email and tool name on every
    request. Both must be supplied explicitly or via configuration
    (NCBI_EMAIL / PUBMED_EMAIL and NCBI_TOOL_NAME); no request is issued when
    either is missing.
    """
    user_email = (
        email
        or os.getenv("NCBI_EMAIL")
        or os.getenv("PUBMED_EMAIL")
        or ""
    ).strip()
    tool_name = (tool or os.getenv("NCBI_TOOL_NAME") or "").strip()

    if not user_email or "@" not in user_email or not tool_name:
        logger.warning(
            "PubMed search skipped: NCBI requires a registered contact email "
            "(NCBI_EMAIL or PUBMED_EMAIL) and a tool name (NCBI_TOOL_NAME)."
        )
        return []

    ncbi_api_key = os.getenv("NCBI_API_KEY", "").strip()

    # Step 1: ESearch to obtain PMIDs
    esearch_url = f"{NCBI_BASE_URL}/esearch.fcgi"
    esearch_params: dict[str, Any] = {
        "db": "pubmed",
        "term": query,
        "retmax": min(limit, 30),
        "retmode": "json",
        "sort": "pub_date",
        "email": user_email,
        "tool": tool_name
    }
    if ncbi_api_key:
        esearch_params["api_key"] = ncbi_api_key

    try:
        async with httpx.AsyncClient(timeout=PUBMED_TIMEOUT, follow_redirects=True) as client:
            search_resp = await _rate_limited_get(client, esearch_url, esearch_params)
            if search_resp.status_code != 200:
                logger.warning(f"PubMed esearch returned status {search_resp.status_code}")
                return []
            
            search_data = search_resp.json()
            id_list = search_data.get("esearchresult", {}).get("idlist", [])
            if not id_list:
                return []

            # Step 2: ESummary to get paper metadata
            esummary_url = f"{NCBI_BASE_URL}/esummary.fcgi"
            esummary_params: dict[str, Any] = {
                "db": "pubmed",
                "id": ",".join(id_list),
                "retmode": "json",
                "email": user_email,
                "tool": tool_name
            }
            if ncbi_api_key:
                esummary_params["api_key"] = ncbi_api_key

            summary_resp = await _rate_limited_get(client, esummary_url, esummary_params)
            if summary_resp.status_code != 200:
                return []
            
            summary_data = summary_resp.json().get("result", {})

            # Step 3: EFetch XML to extract abstracts
            abstracts_map: dict[str, str] = {}
            efetch_url = f"{NCBI_BASE_URL}/efetch.fcgi"
            efetch_params: dict[str, Any] = {
                "db": "pubmed",
                "id": ",".join(id_list),
                "retmode": "xml",
                "email": user_email,
                "tool": tool_name
            }
            if ncbi_api_key:
                efetch_params["api_key"] = ncbi_api_key

            try:
                fetch_resp = await _rate_limited_get(client, efetch_url, efetch_params)
                if fetch_resp.status_code == 200 and fetch_resp.text:
                    root = ET.fromstring(fetch_resp.text)
                    for article in root.findall(".//PubmedArticle"):
                        pmid_elem = article.find(".//MedlineCitation/PMID")
                        if pmid_elem is not None and pmid_elem.text:
                            pmid_val = pmid_elem.text.strip()
                            abstract_texts = article.findall(".//Abstract/AbstractText")
                            if abstract_texts:
                                # itertext() keeps nested inline markup (<i>, <sup>)
                                # and trailing tail text that t.text alone drops.
                                segments = [
                                    " ".join("".join(t.itertext()).split())
                                    for t in abstract_texts
                                ]
                                full_abs = " ".join(s for s in segments if s)
                                abstracts_map[pmid_val] = full_abs
            except Exception as e:  # noqa: BLE001
                logger.debug(f"PubMed efetch abstract extraction fallback: {e}")

            records: list[PaperRecord] = []

            for pmid in id_list:
                item = summary_data.get(pmid)
                if not item or not isinstance(item, dict):
                    continue

                title = item.get("title", "").strip().rstrip(".")
                if not title:
                    continue

                # Authors
                authors = []
                for auth in item.get("authors", []):
                    name = auth.get("name", "").strip()
                    if name:
                        authors.append(name)

                # Year
                pubdate = item.get("pubdate", "")
                year_match = re.search(r"\b(19\d\d|20\d\d)\b", pubdate)
                year = year_match.group(1) if year_match else "n.d."

                # Venue / Source Journal
                venue = item.get("source") or item.get("fulljournalname")

                # Extract DOI if present
                doi = None
                for article_id in item.get("articleids", []):
                    if article_id.get("idtype") == "doi":
                        doi = article_id.get("value")
                        break

                url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                paper_id = make_paper_id(doi=doi, title=title, year=year)
                abstract_text = abstracts_map.get(str(pmid), "")

                record = PaperRecord(
                    paper_id=paper_id,
                    doi=doi,
                    pmid=str(pmid),
                    title=title,
                    authors=authors,
                    year=year,
                    venue=venue,
                    abstract=abstract_text,
                    source_url=url,
                    retrieval_source="pubmed",
                    citation_count=0,
                    screening_status="retrieved",
                )
                records.append(record)

            logger.info(f"PubMed returned {len(records)} papers for: {query[:40]!r}")
            return records

    except Exception as e:  # noqa: BLE001
        logger.warning(f"PubMed search failed for {query[:40]!r}: {e}")
        return []
