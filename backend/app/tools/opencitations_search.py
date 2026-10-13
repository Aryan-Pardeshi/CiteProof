"""OpenCitations API integration for citation graph traversal.

Fetches forward citations (citing papers) and backward references (cited papers)
for seed papers to expand the research corpus via citation graph connectivity.
"""

from __future__ import annotations

import asyncio
import logging
import urllib.parse
from typing import Any, Dict, List, Optional, Set

import httpx

from backend.app.models.evidence import PaperRecord, make_paper_id

logger = logging.getLogger(__name__)

OPENCITATIONS_COCI_BASE = "https://opencitations.net/index/coci/api/v1"
OPENCITATIONS_TIMEOUT = 10.0


def _clean_doi(doi: str) -> str:
    """Canonicalize DOI string for OpenCitations API."""
    if not doi:
        return ""
    d = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d


async def get_citations(doi: str, limit: int = 20, client: Optional[httpx.AsyncClient] = None) -> List[str]:
    """Retrieve list of citing DOIs (forward citations) for a given paper DOI."""
    clean = _clean_doi(doi)
    if not clean:
        return []
    
    encoded_doi = urllib.parse.quote(clean, safe="/")
    url = f"{OPENCITATIONS_COCI_BASE}/citations/{encoded_doi}"
    try:
        if client is not None:
            resp = await client.get(url)
            if resp.status_code != 200:
                return []
            data = resp.json()
            citing_dois = [item.get("citing") for item in data if item.get("citing")]
            return citing_dois[:limit]
        else:
            async with httpx.AsyncClient(timeout=OPENCITATIONS_TIMEOUT, follow_redirects=True) as local_client:
                resp = await local_client.get(url)
                if resp.status_code != 200:
                    return []
                data = resp.json()
                citing_dois = [item.get("citing") for item in data if item.get("citing")]
                return citing_dois[:limit]
    except Exception as e:
        logger.debug(f"OpenCitations citations query failed for {doi}: {e}")
        return []


async def get_references(doi: str, limit: int = 20, client: Optional[httpx.AsyncClient] = None) -> List[str]:
    """Retrieve list of cited reference DOIs (backward references) for a given paper DOI."""
    clean = _clean_doi(doi)
    if not clean:
        return []
    
    encoded_doi = urllib.parse.quote(clean, safe="/")
    url = f"{OPENCITATIONS_COCI_BASE}/references/{encoded_doi}"
    try:
        if client is not None:
            resp = await client.get(url)
            if resp.status_code != 200:
                return []
            data = resp.json()
            cited_dois = [item.get("cited") for item in data if item.get("cited")]
            return cited_dois[:limit]
        else:
            async with httpx.AsyncClient(timeout=OPENCITATIONS_TIMEOUT, follow_redirects=True) as local_client:
                resp = await local_client.get(url)
                if resp.status_code != 200:
                    return []
                data = resp.json()
                cited_dois = [item.get("cited") for item in data if item.get("cited")]
                return cited_dois[:limit]
    except Exception as e:
        logger.debug(f"OpenCitations references query failed for {doi}: {e}")
        return []


async def expand_citation_graph(
    seed_dois: List[str],
    max_expansion: int = 25
) -> Set[str]:
    """Expand citation graph by traversing 1-hop forward citations and backward references.
    
    Returns a set of discovered new candidate DOIs.
    """
    discovered_dois: Set[str] = set()
    if max_expansion <= 0:
        return discovered_dois

    cleaned_seeds = {_clean_doi(d) for d in seed_dois if d}
    target_seeds = seed_dois[:8]
    semaphore = asyncio.Semaphore(4)

    async with httpx.AsyncClient(timeout=OPENCITATIONS_TIMEOUT, follow_redirects=True) as client:
        async def _expand_seed(doi: str) -> List[str]:
            async with semaphore:
                c_dois = await get_citations(doi, limit=10, client=client)
                r_dois = await get_references(doi, limit=10, client=client)
                return c_dois + r_dois

        tasks = [_expand_seed(d) for d in target_seeds]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, list):
                for d in res:
                    clean = _clean_doi(d)
                    if clean and clean not in cleaned_seeds:
                        discovered_dois.add(clean)
                        if len(discovered_dois) >= max_expansion:
                            break
            if len(discovered_dois) >= max_expansion:
                break

    logger.info(f"OpenCitations graph expansion discovered {len(discovered_dois)} connected DOIs")
    return discovered_dois
