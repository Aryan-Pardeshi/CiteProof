"""Crossref API integration for academic paper retrieval and DOI metadata.

Uses Crossref REST API polite pool (mailto header) for high reliability.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

from backend.app.models.evidence import PaperRecord, make_paper_id

logger = logging.getLogger(__name__)

CROSSREF_API_URL = "https://api.crossref.org/works"
CROSSREF_TIMEOUT = 10.0


def _clean_abstract(raw: str) -> str:
    """Strip JATS XML tags (<jats:p>, <jats:title>, etc.) from Crossref abstracts."""
    if not raw:
        return ""
    clean = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", clean).strip()


async def search_crossref(
    query: str,
    limit: int = 15,
    email: Optional[str] = None
) -> List[PaperRecord]:
    """Search Crossref for academic literature matching the query.
    
    Returns a list of structured PaperRecord objects.
    """
    user_email = email or os.getenv("OPENALEX_EMAIL") or "researcher@example.com"
    headers = {
        "User-Agent": f"DeepResearchAssistant/2.0 (mailto:{user_email})"
    }
    params = {
        "query": query,
        "rows": min(limit, 50),
        "sort": "relevance",
        "select": "DOI,title,author,published,published-print,published-online,container-title,abstract,is-referenced-by-count,URL"
    }

    try:
        async with httpx.AsyncClient(timeout=CROSSREF_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(CROSSREF_API_URL, params=params, headers=headers)
            if resp.status_code != 200:
                logger.warning(f"Crossref search returned status {resp.status_code} for query: {query[:40]!r}")
                return []
            
            data = resp.json()
            items = data.get("message", {}).get("items", [])
            records: List[PaperRecord] = []

            for item in items:
                # Title
                titles = item.get("title", [])
                title = titles[0].strip() if titles else ""
                if not title:
                    continue

                # DOI
                doi = item.get("DOI")

                # Authors
                raw_authors = item.get("author", [])
                authors = []
                for a in raw_authors:
                    given = a.get("given", "").strip()
                    family = a.get("family", "").strip()
                    if family and given:
                        authors.append(f"{family}, {given[0]}.")
                    elif family:
                        authors.append(family)
                    elif a.get("name"):
                        authors.append(a["name"].strip())

                # Publication Year
                year = "n.d."
                for date_key in ("published", "published-print", "published-online"):
                    date_parts = item.get(date_key, {}).get("date-parts", [])
                    if date_parts and date_parts[0]:
                        year = str(date_parts[0][0])
                        break

                # Venue
                venues = item.get("container-title", [])
                venue = venues[0] if venues else None

                # Abstract
                abstract = _clean_abstract(item.get("abstract", ""))

                # Citations & URL
                citation_count = int(item.get("is-referenced-by-count", 0) or 0)
                url = item.get("URL") or (f"https://doi.org/{doi}" if doi else "")

                paper_id = make_paper_id(doi=doi, title=title, year=year)

                record = PaperRecord(
                    paper_id=paper_id,
                    doi=doi,
                    title=title,
                    authors=authors,
                    year=year,
                    venue=venue,
                    abstract=abstract,
                    source_url=url,
                    retrieval_source="crossref",
                    citation_count=citation_count,
                    screening_status="retrieved",
                )
                records.append(record)

            logger.info(f"Crossref returned {len(records)} papers for: {query[:40]!r}")
            return records

    except Exception as e:
        logger.warning(f"Crossref search failed for {query[:40]!r}: {e}")
        return []
