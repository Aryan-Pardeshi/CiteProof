import asyncio
import io
import logging
import os
import re
import xml.etree.ElementTree as ET

import httpx
import pypdf

from backend.app.tools.oa_resolver import resolve_oa_pdf_url

logger = logging.getLogger(__name__)

# Open-access URLs are not always trustworthy: OpenAlex oa_url in particular can
# resolve to a proceedings volume or an unrelated record. Text that does not look
# like the paper it is attached to is worse than no text at all, because it grounds
# the literature review in a source the citation does not name.
TITLE_MATCH_THRESHOLD = 0.4    # fraction of title words that must appear up front
TITLE_MATCH_WINDOW = 4000      # characters searched for them; repository deposits
                               # (HAL, institutional archives) prepend a cover sheet
                               # before the paper itself begins

FULLTEXT_TOP_N = 10            # only the N highest-ranked papers get fetched
FULLTEXT_CHAR_BUDGET = 1500    # prompt-facing excerpt characters kept per paper
# Whole-document budget kept for quote anchoring. The prompt-sized excerpt above
# rarely spans past page 1-2, which would make page attribution for quotes from
# later sections impossible; anchoring uses this larger text instead.
FULLTEXT_ANCHOR_CHAR_BUDGET = 40000
FULLTEXT_FETCH_TIMEOUT = 8.0   # seconds, per PDF download
FULLTEXT_MAX_PDF_BYTES = 16 * 1024 * 1024  # 16MB cap, abort larger downloads

FULLTEXT_CONCURRENCY = 4       # concurrent downloads
FULLTEXT_TOTAL_BUDGET_SECONDS = 25.0   # whole-batch ceiling


def _extract_xml_text(raw: bytes) -> str:
    """Flattens JATS full-text XML into plain text.

    Europe PMC's open-access endpoint returns article XML rather than a PDF, so the
    body has to be walked directly. Falls back to the whole document when no <body>
    element is present.
    """
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        logger.warning(f"Could not parse full-text XML: {e}")
        return ""
    # The article title sits in <front>, not <body>. Prepend it so the
    # title-match guard sees it and the excerpt opens with the paper's own title.
    title_el = root.find(".//article-title")
    title = " ".join(title_el.itertext()) if title_el is not None else ""

    body = root.find(".//body")
    node = body if body is not None else root
    return " ".join(f"{title} {' '.join(node.itertext())}".split())


def _significant_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower())}


def _text_belongs_to_paper(title: str, text: str) -> bool:
    """Checks the extracted PDF text really is the paper it claims to be.

    A paper's own title appears near the front of its PDF, so most title words
    should turn up in the opening window. When they do not, the URL resolved to a
    different document entirely.
    """
    title_words = _significant_words(title)
    if not title_words:
        return True  # nothing to check against; do not discard on a missing title
    head_words = _significant_words(text[:TITLE_MATCH_WINDOW])
    overlap = len(title_words & head_words) / len(title_words)
    return overlap >= TITLE_MATCH_THRESHOLD


def _skip_cover_page(title: str, text: str) -> str:
    """Drops repository boilerplate preceding the paper.

    HAL and similar archives prepend a deposit cover sheet; starting the excerpt at
    the paper's own title spends the character budget on content instead of
    submission metadata.
    """
    words = [w for w in re.findall(r"[a-z]{4,}", (title or "").lower())][:4]
    if len(words) < 3:
        return text
    # Allow short connecting words between the significant ones: "Applied to Document"
    pattern = r".{0,30}?".join(re.escape(w) for w in words)
    match = re.search(pattern, text[:TITLE_MATCH_WINDOW], re.IGNORECASE | re.DOTALL)
    return text[match.start():] if match else text


async def _download_and_extract(client: httpx.AsyncClient, url: str, title: str) -> str | None:
    if not url:
        return None
    try:
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                logger.warning(f"HTTP {resp.status_code} fetching PDF for '{title}' from {url}")
                return None

            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit() and int(cl) > FULLTEXT_MAX_PDF_BYTES:
                logger.warning(
                    f"Aborting fetch for '{title}': Content-Length {cl} exceeds {FULLTEXT_MAX_PDF_BYTES} bytes cap."
                )
                return None

            chunks = []
            downloaded = 0
            async for chunk in resp.aiter_bytes():
                downloaded += len(chunk)
                if downloaded > FULLTEXT_MAX_PDF_BYTES:
                    logger.warning(
                        f"Aborting fetch for '{title}': Downloaded size exceeded {FULLTEXT_MAX_PDF_BYTES} bytes cap."
                    )
                    return None
                chunks.append(chunk)
            pdf_bytes = b"".join(chunks)

        if not pdf_bytes:
            logger.warning(f"Empty PDF content received for '{title}'")
            return None

        if pdf_bytes[:5] != b"%PDF-" and pdf_bytes.lstrip()[:5] == b"<?xml":
            # Europe PMC serves open-access full text as JATS XML, not PDF
            full_text = _extract_xml_text(pdf_bytes)
        else:
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            # Normalize each page separately but keep page boundaries as
            # form-feed markers so downstream evidence extraction can attribute
            # a quoted span to its exact source page.
            pages_text = []
            for page in reader.pages:
                txt = page.extract_text()
                # Keep image-only/empty pages as empty entries: dropping one
                # would also drop its form-feed boundary and shift every later
                # page number down by one.
                pages_text.append(" ".join(txt.split()) if txt else "")
            full_text = "\f".join(pages_text)
        if not full_text or not full_text.replace("\f", "").strip():
            logger.warning(f"No text extracted from PDF for '{title}'")
            return None

        if not _text_belongs_to_paper(title, full_text):
            logger.warning(
                f"Discarding full text for '{title}': extracted content does not match "
                f"the paper title (URL likely resolved to a different document)."
            )
            return None

        # Return the whole title-checked document (within the anchor budget)
        # with page markers intact; callers slice prompt-sized excerpts.
        anchored_text = _skip_cover_page(title, full_text)
        return anchored_text[:FULLTEXT_ANCHOR_CHAR_BUDGET]
    except Exception as e:
        logger.warning(f"Failed to fetch/extract fulltext PDF for '{title}' from {url}: {e}")
        return None


async def _fetch_single_pdf(paper: dict, semaphore: asyncio.Semaphore) -> tuple[str, str | None]:
    """Attempts to fetch, resolve via OA fallback, download, and extract text for a single paper."""
    pid = paper.get("paper_id") or paper.get("id") or paper.get("title", "")
    title = paper.get("title", "")
    initial_pdf_url = paper.get("pdf_url", "")

    email = os.getenv("OPENALEX_EMAIL", "").strip()
    user_agent = f"ResearchBot/1.0 (mailto:{email})" if (email and email != "your_email@example.com") else "ResearchBot/1.0 (mailto:researchbot@example.com)"
    headers = {"User-Agent": user_agent}

    async with semaphore:
        try:
            async with httpx.AsyncClient(timeout=FULLTEXT_FETCH_TIMEOUT, follow_redirects=True, headers=headers) as client:
                if initial_pdf_url:
                    excerpt = await _download_and_extract(client, initial_pdf_url, title)
                    if excerpt:
                        return (pid, excerpt)
                    logger.info(f"Direct fetch failed for '{title}'. Attempting OA resolution...")

                paper_for_oa = dict(paper)
                paper_for_oa["pdf_url"] = ""
                resolved_url = await resolve_oa_pdf_url(client, paper_for_oa)

                if resolved_url and resolved_url != initial_pdf_url:
                    logger.info(f"Retrying fetch for '{title}' with resolved OA URL: {resolved_url}")
                    excerpt = await _download_and_extract(client, resolved_url, title)
                    if excerpt:
                        return (pid, excerpt)

                return (pid, None)
        except Exception as e:
            logger.warning(f"Error processing PDF for '{title}': {e}")
            return (pid, None)


async def fetch_fulltext_excerpts(papers: list[dict]) -> dict[str, str]:
    """Fetches page-marked PDF full texts concurrently for the top FULLTEXT_TOP_N papers."""
    top_selected = papers[:FULLTEXT_TOP_N]
    results: dict[str, str] = {}
    if not top_selected:
        logger.info(f"fetched full text for 0/{FULLTEXT_TOP_N} papers")
        return results

    semaphore = asyncio.Semaphore(FULLTEXT_CONCURRENCY)
    tasks = [asyncio.create_task(_fetch_single_pdf(p, semaphore)) for p in top_selected]

    try:
        raw_results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=FULLTEXT_TOTAL_BUDGET_SECONDS
        )
        for res in raw_results:
            if isinstance(res, tuple) and res[1] is not None:
                pid, excerpt = res
                results[pid] = excerpt
    except (asyncio.TimeoutError, asyncio.CancelledError):
        logger.warning(f"Batch full-text fetch timed out or cancelled after {FULLTEXT_TOTAL_BUDGET_SECONDS}s")
        for t in tasks:
            if not t.done():
                t.cancel()
            else:
                try:
                    if not t.cancelled():
                        res = t.result()
                        if isinstance(res, tuple) and res[1] is not None:
                            results[res[0]] = res[1]
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Error in fetch_fulltext_excerpts batch: {e}")

    logger.info(f"fetched full text for {len(results)}/{FULLTEXT_TOP_N} papers")
    return results


async def fetch_fulltexts(papers: list[dict]) -> list[dict]:
    """Fetches full text and attaches it to paper dictionaries.

    'full_text' carries the page-marked anchor text (up to
    FULLTEXT_ANCHOR_CHAR_BUDGET) for quote attribution; 'fulltext_excerpt' and
    'content_excerpt' stay prompt-sized (FULLTEXT_CHAR_BUDGET).
    """
    excerpts_map = await fetch_fulltext_excerpts(papers)
    enriched = []
    for p in papers:
        p_copy = dict(p)
        pid = p_copy.get("paper_id") or p_copy.get("id") or p_copy.get("title", "")
        if pid in excerpts_map:
            full_text = excerpts_map[pid]
            excerpt = full_text[:FULLTEXT_CHAR_BUDGET]
            p_copy["full_text"] = full_text
            p_copy["fulltext_excerpt"] = excerpt
            p_copy["content_excerpt"] = excerpt
        enriched.append(p_copy)
    return enriched
