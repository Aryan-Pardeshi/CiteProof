"""Academic search orchestration, multi-index retrieval, deduplication, and screening.

Integrates OpenAlex, Semantic Scholar, ArXiv, Europe PMC, DOAJ, DataCite,
Crossref, and PubMed with deterministic PRISMA 2020 tracking and structured
PaperRecord outputs. Each provider lives in its own adapter module under
backend/app/tools/ and fails independently: an exception or timeout in any
single provider never aborts the fan-out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

import httpx

try:
    import feedparser
except ImportError:
    feedparser = None

from backend.app.llm import get_llm
from backend.app.models.evidence import PaperRecord, PRISMATracker, make_paper_id
from backend.app.tools.crossref_search import search_crossref
from backend.app.tools.datacite_search import search_datacite
from backend.app.tools.doaj_search import search_doaj
from backend.app.tools.europe_pmc_search import search_europe_pmc
from backend.app.tools.openalex_search import search_openalex
from backend.app.tools.pubmed_search import search_pubmed
from backend.app.tools.semantic_scholar_search import search_semantic_scholar

logger = logging.getLogger(__name__)

# Strict 6 second HTTP timeout to prevent hanging
HTTP_TIMEOUT = 6.0

# Corpus limits for the screening stage
MAX_SEARCH_KEYWORDS = 10      # keywords queried against every index
SCREEN_THRESHOLD = 30        # corpus sizes at or below this skip screening entirely
SCREEN_CANDIDATES = 80       # papers sent to the LLM relevance scorer (raised to include new sources)
SCREEN_KEEP = 40             # papers retained for synthesis
SCREEN_MIN_PER_SOURCE = 2   # guaranteed minimum papers kept per source after screening
SCREEN_BATCH_SIZE = 15       # papers per LLM scoring call
SCREEN_CONCURRENCY = 4       # scoring calls in flight at once


def _normalize_doi(doi: str | None) -> str:
    """Canonicalize DOI string."""
    if not doi:
        return ""
    doi = doi.strip().lower()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi)
    return doi.strip()


def _normalize_title(title: str | None) -> str:
    """Normalize title for fuzzy collision-resistant deduplication."""
    if not title:
        return ""
    return re.sub(r"\W+", "", title.lower())


async def fetch_arxiv_papers(client: httpx.AsyncClient, keyword: str, max_results: int = 50) -> list[PaperRecord]:
    """Retrieve papers from arXiv."""
    papers = []
    if feedparser is None:
        logger.warning(
            f"ArXiv search skipped for '{keyword}': optional dependency 'feedparser' is not installed."
        )
        return papers
    try:
        clean_kw = re.sub(r"[^\w\s]", "", keyword).strip()
        url = f"http://export.arxiv.org/api/query?search_query=all:{clean_kw}&start=0&max_results={min(max_results, 50)}&sortBy=relevance"
        
        resp = await client.get(url)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.text)
            for entry in feed.entries:
                title = entry.get("title", "").replace("\n", " ").strip()
                if not title:
                    continue
                abstract = entry.get("summary", "").replace("\n", " ").strip()
                authors = [a.get("name", "") for a in entry.get("authors", []) if a.get("name")]
                published = entry.get("published", "")
                year = published[:4] if len(published) >= 4 else "n.d."
                arxiv_id = entry.get("id", "")
                doi = _normalize_doi(entry.get("arxiv_doi"))

                paper_id = make_paper_id(doi=doi, title=title, year=year)
                papers.append(PaperRecord(
                    paper_id=paper_id,
                    doi=doi or None,
                    arxiv_id=arxiv_id.split("/")[-1] if arxiv_id else None,
                    title=title,
                    abstract=abstract,
                    authors=authors,
                    year=year,
                    source_url=arxiv_id,
                    pdf_url=arxiv_id.replace("/abs/", "/pdf/") if arxiv_id else None,
                    retrieval_source="arxiv",
                    citation_count=0,
                    study_type="theoretical",
                    screening_status="retrieved"
                ))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"ArXiv search skipped for '{keyword}': {e}")
    return papers


async def fetch_tavily_web_papers(keyword: str, max_results: int = 5) -> list[PaperRecord]:
    """LAST RESORT FALLBACK: Web articles via Tavily when academic databases return insufficient literature."""
    papers = []
    try:
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            return papers
        from tavily import TavilyClient
        logger.info(f"Triggering LAST RESORT Tavily web search fallback for keyword: '{keyword}'")
        tavily = TavilyClient(api_key=api_key)
        res = tavily.search(query=f"academic research paper {keyword}", max_results=max_results, search_depth="basic")
        results = res.get("results", []) if isinstance(res, dict) else []
        for r in results:
            title = r.get("title", "").strip()
            content = r.get("content", "").strip()
            url = r.get("url", "").strip()
            if not title or not content:
                continue
            paper_id = make_paper_id(doi=None, title=title, year="n.d.")
            papers.append(PaperRecord(
                paper_id=paper_id,
                title=title,
                abstract=content,
                authors=["Web Source"],
                source_url=url,
                pdf_url=url if url.lower().endswith(".pdf") else None,
                retrieval_source="tavily_web_fallback",
                citation_count=0,
                screening_status="retrieved"
            ))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Tavily web fallback search skipped for '{keyword}': {e}")
    return papers


async def search_academic_papers_structured(
    keywords: list[str]
) -> tuple[list[PaperRecord], PRISMATracker]:
    """Concurrent multi-source academic search returning typed PaperRecords and PRISMATracker."""
    selected_keywords = keywords[:MAX_SEARCH_KEYWORDS] if keywords else ["research"]
    records_by_source: dict[str, int] = {
        "openalex": 0,
        "semantic_scholar": 0,
        "arxiv": 0,
        "europe_pmc": 0,
        "doaj": 0,
        "datacite": 0,
        "crossref": 0,
        "pubmed": 0,
        "tavily_web_fallback": 0,
    }

    # Each adapter manages its own HTTP client and timeout (same contract as
    # search_crossref / search_pubmed) so a slow or hanging provider cannot
    # stall the others through a shared pool. ArXiv keeps its inline
    # feedparser-based fetcher and shares one client across keywords.
    tasks = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as arxiv_client:
        for kw in selected_keywords:
            tasks.append(search_openalex(kw, limit=15))
            tasks.append(search_semantic_scholar(kw, limit=15))
            tasks.append(fetch_arxiv_papers(arxiv_client, kw))
            tasks.append(search_europe_pmc(kw, limit=15))
            tasks.append(search_doaj(kw, limit=15))
            tasks.append(search_datacite(kw, limit=15))
            tasks.append(search_crossref(kw, limit=15))
            tasks.append(search_pubmed(kw, limit=15))

        results_list = await asyncio.gather(*tasks, return_exceptions=True)
        combined: list[PaperRecord] = []
        for res in results_list:
            if isinstance(res, list):
                for p in res:
                    if isinstance(p, PaperRecord):
                        combined.append(p)
                        src = p.retrieval_source
                        records_by_source[src] = records_by_source.get(src, 0) + 1

    # Deduplicate by DOI and normalized title
    seen_dois = set()
    seen_titles = set()
    seen_paper_ids = set()
    deduped: list[PaperRecord] = []

    for p in combined:
        doi = p.doi
        norm_title = _normalize_title(p.title)
        pid = p.paper_id

        if pid in seen_paper_ids:
            continue
        if doi and doi in seen_dois:
            continue
        if norm_title and norm_title in seen_titles:
            continue

        seen_paper_ids.add(pid)
        if doi:
            seen_dois.add(doi)
        if norm_title:
            seen_titles.add(norm_title)

        deduped.append(p)

    # LAST RESORT FALLBACK: If academic indexes returned fewer than 5 papers, trigger Tavily
    if len(deduped) < 5 and selected_keywords:
        logger.warning(f"Academic search yield low ({len(deduped)} papers). Invoking Tavily web fallback...")
        tavily_tasks = [fetch_tavily_web_papers(kw, max_results=5) for kw in selected_keywords[:3]]
        tavily_results = await asyncio.gather(*tavily_tasks, return_exceptions=True)
        for res in tavily_results:
            if isinstance(res, list):
                for p in res:
                    norm_title = _normalize_title(p.title)
                    if norm_title and norm_title not in seen_titles:
                        seen_titles.add(norm_title)
                        seen_paper_ids.add(p.paper_id)
                        combined.append(p)
                        deduped.append(p)
                        records_by_source["tavily_web_fallback"] = records_by_source.get("tavily_web_fallback", 0) + 1

    duplicates_count = max(0, len(combined) - len(deduped))
    tracker = PRISMATracker(
        records_identified=len(combined),
        records_by_source=records_by_source,
        duplicates_removed=duplicates_count,
        records_after_dedup=len(deduped),
        records_screened=0,
        excluded_title_abstract=0,
        full_text_requested=0,
        full_text_assessed=0,
        excluded_full_text=0,
        studies_included=0,
    )

    logger.info(
        f"Search complete: {len(combined)} identified, {duplicates_count} duplicates removed, "
        f"{len(deduped)} retained after deduplication."
    )
    return deduped, tracker


async def search_academic_papers(
    keywords: list[str]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Legacy adapter returning dictionary papers and stats for backward compatibility."""
    records, tracker = await search_academic_papers_structured(keywords)
    dict_papers = [r.model_dump() for r in records]
    stats = {
        "retrieved": tracker.records_identified,
        "after_dedup": tracker.records_after_dedup,
        "screened": tracker.records_screened,
        "included": tracker.studies_included,
    }
    return dict_papers, stats


def _select_candidates(papers: list[PaperRecord], limit: int) -> list[PaperRecord]:
    """Select candidate pool for screening using round-robin distribution across sources."""
    by_source: dict[str, list[PaperRecord]] = {}
    for p in papers:
        by_source.setdefault(p.retrieval_source, []).append(p)

    for bucket in by_source.values():
        bucket.sort(key=lambda p: p.citation_count or 0, reverse=True)

    candidates: list[PaperRecord] = []
    buckets = list(by_source.values())
    idx = 0
    while len(candidates) < limit and any(idx < len(b) for b in buckets):
        for bucket in buckets:
            if idx < len(bucket):
                candidates.append(bucket[idx])
                if len(candidates) >= limit:
                    break
        idx += 1
    return candidates


async def _score_batch(
    llm,
    batch: list[PaperRecord],
    problem_statement: str,
    objectives_str: str,
    semaphore: asyncio.Semaphore
) -> list[PaperRecord]:
    """Scores one batch of papers 1-10 for relevance."""
    prompt = f"""Problem Statement:
{problem_statement}

Research Objectives:
{objectives_str}

Evaluate the following candidate papers for relevance to the Problem Statement and Objectives.
For each paper, return a score from 1 to 10 (10 = highly relevant, 1 = irrelevant).

Papers:
"""
    for idx, p in enumerate(batch):
        prompt += f"\n[{idx+1}] Title: {p.title}\nAbstract snippet: {(p.abstract or '')[:300]}\n"

    prompt += """\nReturn ONLY a JSON array of objects with fields 'id' (1-based index) and 'score' (1-10). Example:
[{"id": 1, "score": 9}, {"id": 2, "score": 4}]"""

    score_map: dict[int, int] = {}
    async with semaphore:
        try:
            res = await asyncio.wait_for(llm.ainvoke(prompt), timeout=20.0)
            raw = str(res.content).strip()
            if "```" in raw:
                raw = raw.split("```")[1]
                raw = raw.removeprefix("json")
            scores = json.loads(raw.strip())
            score_map = {
                item["id"]: item.get("score", 5)
                for item in scores
                if isinstance(item, dict) and "id" in item
            }
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Screening batch scoring defaulted to neutral [5]: {e}")

    scored = []
    for idx, p in enumerate(batch):
        p_copy = p.model_copy(deep=True)
        p_copy.relevance_score = float(score_map.get(idx + 1, 5))
        scored.append(p_copy)
    return scored


async def screen_papers_structured(
    papers: list[PaperRecord],
    problem_statement: str,
    objectives: list[str],
    tracker: PRISMATracker | None = None,
    model: str | None = None
) -> tuple[list[PaperRecord], PRISMATracker]:
    """Screen candidate corpus for relevance, updating PRISMATracker deterministically."""
    tr = tracker.model_copy(deep=True) if tracker else PRISMATracker(
        records_identified=len(papers),
        records_after_dedup=len(papers)
    )

    if len(papers) <= SCREEN_THRESHOLD:
        tr.records_screened = len(papers)
        tr.excluded_title_abstract = 0
        tr.full_text_requested = len(papers)
        tr.full_text_assessed = len(papers)
        tr.studies_included = len(papers)
        copied_papers = []
        for p in papers:
            p_copy = p.model_copy(deep=True)
            p_copy.screening_status = "included"
            copied_papers.append(p_copy)
        return copied_papers, tr

    candidates = _select_candidates(papers, SCREEN_CANDIDATES)
    tr.records_screened = len(papers)
    logger.info(f"Screening {len(candidates)} candidates for relevance against problem statement...")

    llm = get_llm(model=model, role="researcher")
    objectives_str = "\n".join(f"- {obj}" for obj in objectives)
    semaphore = asyncio.Semaphore(SCREEN_CONCURRENCY)

    batches = [
        candidates[i:i + SCREEN_BATCH_SIZE]
        for i in range(0, len(candidates), SCREEN_BATCH_SIZE)
    ]

    tasks = [
        _score_batch(llm, b, problem_statement, objectives_str, semaphore)
        for b in batches
    ]

    results = await asyncio.gather(
        *tasks,
        return_exceptions=True
    )

    scored_papers: list[PaperRecord] = []
    for res in results:
        if isinstance(res, list):
            scored_papers.extend(res)
        else:
            logger.warning(f"Screening batch raised: {res}")

    if not scored_papers:
        scored_papers = [p.model_copy(deep=True) for p in candidates]

    scored_papers.sort(
        key=lambda p: (p.relevance_score or 0, p.citation_count or 0),
        reverse=True
    )

    # Primary selection: top SCREEN_KEEP by score
    top_pool = scored_papers[:SCREEN_KEEP]
    top_ids = {id(p) for p in top_pool}

    # Guaranteed minimum per source: ensure every source that made it into
    # candidates gets at least SCREEN_MIN_PER_SOURCE papers in the included
    # set, even if they scored below the cutoff. This prevents Crossref /
    # PubMed / OpenCitations from being completely eliminated by citation-count
    # bias (older indexed sources tend to have higher citation counts).
    by_source: dict[str, list[PaperRecord]] = {}
    for p in scored_papers:
        by_source.setdefault(p.retrieval_source, []).append(p)

    guaranteed: list[PaperRecord] = []
    for src_papers in by_source.values():
        present = [p for p in src_papers if id(p) in top_ids]
        if len(present) < SCREEN_MIN_PER_SOURCE:
            deficit = SCREEN_MIN_PER_SOURCE - len(present)
            extras = [p for p in src_papers if id(p) not in top_ids][:deficit]
            guaranteed.extend(extras)

    included_set = list(top_pool)
    g_ids = {id(p) for p in included_set}
    for p in guaranteed:
        if id(p) not in g_ids:
            included_set.append(p)
            g_ids.add(id(p))

    included = included_set
    excluded = [p for p in scored_papers if id(p) not in g_ids]
    excluded_count = max(0, len(candidates) - len(included))
    # Deduplicated papers that never entered the candidate pool are still
    # screened records in PRISMA terms: account for them as title/abstract
    # exclusions so records_screened - excluded == full_text_requested holds.
    unscreened_surplus = max(0, len(papers) - len(candidates))

    for p in included:
        p.screening_status = "included"
    for p in excluded:
        p.screening_status = "excluded"
        p.exclusion_reason = "Low relevance score during title/abstract screening"

    tr.excluded_title_abstract = excluded_count + unscreened_surplus
    tr.full_text_requested = len(included)
    tr.full_text_assessed = len(included)
    tr.studies_included = len(included)
    tr.exclusion_reasons["Title/abstract irrelevance"] = excluded_count
    if unscreened_surplus:
        tr.exclusion_reasons["Not screened: candidate pool limit"] = unscreened_surplus

    logger.info(
        f"Screening complete: {len(included)} included ({len(guaranteed)} via source-guarantee), "
        f"{excluded_count} excluded, {unscreened_surplus} not screened (candidate pool limit)."
    )
    return included, tr


async def screen_papers(
    papers: list[dict[str, Any]],
    problem_statement: str,
    objectives: list[str],
    model: str | None = None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Legacy screening adapter returning dicts and stats."""
    records = [PaperRecord.from_dict(p) for p in papers]
    screened_records, tracker = await screen_papers_structured(records, problem_statement, objectives, model=model)
    dict_screened = [r.model_dump() for r in screened_records]
    stats = {
        "screened": tracker.records_screened,
        "included": tracker.studies_included,
        "retrieved": tracker.records_identified,
        "after_dedup": tracker.records_after_dedup,
    }
    return dict_screened, stats


def format_apa(paper: dict[str, Any]) -> str:
    """Format paper metadata into APA 7th edition citation string."""
    authors = paper.get("authors") or []
    if not authors:
        author_str = "Anonymous"
    elif len(authors) == 1:
        author_str = authors[0]
    elif len(authors) == 2:
        author_str = f"{authors[0]} & {authors[1]}"
    elif len(authors) <= 7:
        author_str = ", ".join(authors[:-1]) + f", & {authors[-1]}"
    else:
        author_str = ", ".join(authors[:6]) + ", et al."

    year = paper.get("year", "n.d.")
    title = str(paper.get("title", "")).strip().rstrip(".")
    doi = paper.get("doi")
    url = paper.get("url") or paper.get("source_url")

    source = str(paper.get("venue") or paper.get("source", "")).strip()
    link = f"https://doi.org/{doi}" if doi else (url or "")

    apa_str = f"{author_str} ({year}). {title}."
    if source:
        apa_str += f" {source}."
    if link:
        apa_str += f" {link}"

    return apa_str.strip()
