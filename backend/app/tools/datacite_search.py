"""DataCite REST API integration for DOI metadata retrieval and enrichment.

Searches DataCite-registered DOIs (datasets, software, preprints, and other
research outputs that Crossref does not index). The DataCite schema is
deliberately NOT assumed to match Crossref's: records arrive in a JSON:API
envelope (data[] -> attributes{}) with different field names and semantics.
No API key required for reads.

Raw API field -> PaperRecord mapping (verified against live API 2026-08):
    data[].attributes.doi                  -> doi          (already bare, normalized defensively)
    data[].attributes.titles[]             -> title        (first entry without titleType, else first entry)
    data[].attributes.creators[].name      -> authors      (fallback: "{givenName} {familyName}" when name absent)
    data[].attributes.publicationYear      -> year         (fallback: dates[] dateType=="Issued" [:4]; "n.d.")
    data[].attributes.container.title      -> venue        (often null; falls back to publisher)
    data[].attributes.descriptions[] where descriptionType=="Abstract" -> abstract
    data[].attributes.url                  -> source_url   (landing page)
    data[].attributes.citationCount        -> citation_count
    (constant)                             -> retrieval_source="datacite", screening_status="retrieved"
    paper_id                               -> make_paper_id(doi, title, year)
DataCite-only fields deliberately dropped (must not leak past the adapter):
identifiers, subjects, rightsList, fundingReferences, geoLocations, sizes,
formats, version, relatedIdentifiers/relatedItems, viewCount/downloadCount
(usage metrics are not citation counts), state/isActive lifecycle flags.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from backend.app.models.evidence import PaperRecord, make_paper_id

logger = logging.getLogger(__name__)

DATACITE_API_URL = "https://api.datacite.org/dois"
DATACITE_TIMEOUT = 12.0


def _normalize_doi(doi: Optional[str]) -> str:
    """Canonicalize a DOI string to bare lowercase form."""
    if not doi:
        return ""
    doi = doi.strip().lower()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi)
    return doi.strip()


def _pick_title(titles: Any) -> str:
    """First plain title (no titleType), else the first title at all."""
    if not isinstance(titles, list):
        return ""
    plain = [t.get("title", "").strip() for t in titles if isinstance(t, dict) and t.get("title")]
    if not plain:
        return ""
    for t in titles:
        if isinstance(t, dict) and t.get("title") and not t.get("titleType"):
            return t["title"].strip()
    return plain[0]


def _extract_authors(creators: Any) -> List[str]:
    """Creator 'name' when present; compose from given/family names otherwise."""
    authors: List[str] = []
    for c in creators or []:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip()
        if not name:
            given = (c.get("givenName") or "").strip()
            family = (c.get("familyName") or "").strip()
            name = f"{given} {family}".strip()
        if name:
            authors.append(name)
    return authors


def _extract_abstract(descriptions: Any) -> str:
    """Concatenate description entries typed as Abstract."""
    parts: List[str] = []
    for d in descriptions or []:
        if isinstance(d, dict) and d.get("descriptionType") == "Abstract" and d.get("description"):
            parts.append(str(d["description"]))
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _extract_year(attributes: Dict[str, Any]) -> str:
    pub_year = attributes.get("publicationYear")
    if pub_year:
        return str(pub_year)
    for d in attributes.get("dates") or []:
        if isinstance(d, dict) and d.get("dateType") == "Issued" and d.get("date"):
            issued = str(d["date"])
            return issued[:4] if len(issued) >= 4 else "n.d."
    return "n.d."


async def search_datacite(
    query: str,
    limit: int = 15,
    client: Optional[httpx.AsyncClient] = None,
) -> List[PaperRecord]:
    """Search DataCite DOIs matching the query.

    Returns structured PaperRecord objects; failures yield [].
    """
    params: Dict[str, Any] = {
        "query": query,
        "page[size]": min(limit, 50),
        "page[number]": 1,
    }

    try:
        if client is not None:
            resp = await client.get(DATACITE_API_URL, params=params)
        else:
            async with httpx.AsyncClient(timeout=DATACITE_TIMEOUT, follow_redirects=True) as own_client:
                resp = await own_client.get(DATACITE_API_URL, params=params)

        if resp.status_code != 200:
            logger.warning(
                f"DataCite search returned status {resp.status_code} for query: {query[:40]!r}"
            )
            return []

        data = resp.json()
        items = data.get("data", [])
        records: List[PaperRecord] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            attributes = item.get("attributes")
            if not isinstance(attributes, dict):
                continue

            title = _pick_title(attributes.get("titles"))
            if not title:
                continue

            doi = _normalize_doi(attributes.get("doi")) or None
            year = _extract_year(attributes)
            abstract = _extract_abstract(attributes.get("descriptions"))

            container = attributes.get("container") or {}
            venue = (
                container.get("title")
                if isinstance(container, dict) and container.get("title")
                else attributes.get("publisher") or None
            )

            citation_count = int(attributes.get("citationCount", 0) or 0)

            paper_id = make_paper_id(doi=doi, title=title, year=year)
            records.append(PaperRecord(
                paper_id=paper_id,
                doi=doi,
                title=re.sub(r"\s+", " ", title),
                authors=_extract_authors(attributes.get("creators")),
                year=year,
                venue=venue,
                abstract=abstract,
                source_url=str(attributes.get("url") or ""),
                retrieval_source="datacite",
                citation_count=citation_count,
                screening_status="retrieved",
            ))

        logger.info(f"DataCite returned {len(records)} papers for: {query[:40]!r}")
        return records

    except Exception as e:
        logger.warning(f"DataCite search failed for {query[:40]!r}: {e}")
        return []
