import logging
import os

import httpx

logger = logging.getLogger(__name__)


async def resolve_unpaywall(client: httpx.AsyncClient, paper: dict) -> str:
    """Resolves an open-access PDF URL via the Unpaywall REST API v2.

    Requires paper['doi'] and a valid OPENALEX_EMAIL configured in the environment.
    Tries best_oa_location first, then repository locations in oa_locations.
    """
    doi = paper.get("doi")
    if not doi:
        return ""

    email = os.getenv("OPENALEX_EMAIL", "").strip()
    if not email or email == "your_email@example.com":
        logger.debug("Skipping Unpaywall resolution: OPENALEX_EMAIL not configured or is placeholder.")
        return ""

    try:
        url = f"https://api.unpaywall.org/v2/{doi}"
        resp = await client.get(url, params={"email": email})
        if resp.status_code == 200:
            data = resp.json()
            oa_locs = data.get("oa_locations") or []

            # 1. Prefer repository pdf URLs if publisher is known to paywall/403
            repo_urls = [
                loc.get("url_for_pdf") or loc.get("url")
                for loc in oa_locs
                if loc.get("host_type") == "repository" and (loc.get("url_for_pdf") or loc.get("url"))
            ]
            if repo_urls:
                return repo_urls[0]

            # 2. Fall back to best_oa_location
            best_loc = data.get("best_oa_location") or {}
            pdf_url = best_loc.get("url_for_pdf") or best_loc.get("url") or ""
            if pdf_url:
                return pdf_url

            # 3. Fall back to any location in oa_locations
            for loc in oa_locs:
                u = loc.get("url_for_pdf") or loc.get("url")
                if u:
                    return u
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Unpaywall resolver error for DOI {doi}: {e}")
    return ""


async def resolve_europe_pmc(client: httpx.AsyncClient, paper: dict) -> str:
    """Resolves an open-access PDF URL via the Europe PMC REST search API."""
    doi = paper.get("doi")
    if not doi:
        return ""

    try:
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        params = {"query": f'DOI:"{doi}"', "format": "json", "resultType": "core"}
        resp = await client.get(url, params=params)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("resultList", {}).get("result", [])
            if results:
                first = results[0]
                is_oa = str(first.get("isOpenAccess", "")).upper() == "Y"
                pmcid = first.get("pmcid", "").strip()
                if pmcid and is_oa:
                    # The fullTextPDF endpoint 404s for these records; fullTextXML is
                    # the one Europe PMC actually serves, and it carries the whole body.
                    return f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Europe PMC resolver error for DOI {doi}: {e}")
    return ""


async def resolve_core(client: httpx.AsyncClient, paper: dict) -> str:
    """Resolves an open-access PDF URL via the CORE v3 search API."""
    core_key = os.getenv("CORE_API_KEY", "").strip()
    if not core_key or core_key in {"your_api_key_here", "your_key_here"}:
        return ""

    doi = paper.get("doi", "").strip()
    title = paper.get("title", "").strip()
    if doi:
        q_str = f'doi:"{doi}"'
    elif title:
        q_str = f'title:"{title}"'
    else:
        return ""

    try:
        url = "https://api.core.ac.uk/v3/search/works"
        headers = {"Authorization": f"Bearer {core_key}"}
        payload = {"q": q_str, "limit": 1}
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if results and isinstance(results, list):
                download_url = results[0].get("downloadUrl") or results[0].get("download_url") or ""
                return download_url
    except Exception as e:  # noqa: BLE001
        logger.debug(f"CORE resolver error for paper '{title}': {e}")
    return ""


async def resolve_oa_pdf_url(client: httpx.AsyncClient, paper: dict) -> str:
    """Tries resolvers in sequence: existing pdf_url, Unpaywall, Europe PMC, CORE.

    Returns the first non-empty URL found and logs at debug level which resolver won.
    """
    existing_url = paper.get("pdf_url", "").strip()
    if existing_url:
        logger.debug(f"resolve_oa_pdf_url: using existing pdf_url for '{paper.get('title')}'")
        return existing_url

    # Try Unpaywall
    unpaywall_url = await resolve_unpaywall(client, paper)
    if unpaywall_url:
        logger.debug(f"resolve_oa_pdf_url: Unpaywall resolver won for '{paper.get('title')}' -> {unpaywall_url}")
        return unpaywall_url

    # Try Europe PMC
    europe_pmc_url = await resolve_europe_pmc(client, paper)
    if europe_pmc_url:
        logger.debug(f"resolve_oa_pdf_url: Europe PMC resolver won for '{paper.get('title')}' -> {europe_pmc_url}")
        return europe_pmc_url

    # Try CORE
    core_url = await resolve_core(client, paper)
    if core_url:
        logger.debug(f"resolve_oa_pdf_url: CORE resolver won for '{paper.get('title')}' -> {core_url}")
        return core_url

    logger.debug(f"resolve_oa_pdf_url: No OA PDF URL resolved for '{paper.get('title')}'")
    return ""
