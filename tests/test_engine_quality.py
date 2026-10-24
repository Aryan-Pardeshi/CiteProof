"""Offline regression tests for the engine-quality fixes.

Covers the report guard, bounded LLM retries and unsafe model ids, Tavily
argument tolerance, screening calibration and PRISMA accounting, and the
Research Mode paper-quality writers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from pathlib import Path

import pytest

# Add the repo root to sys.path so 'backend' module is importable.
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from backend.app import llm as llm_module
from backend.app.agents.research_mode import _common, retrieval, synthesis, writing
from backend.app.llm import (
    ainvoke_llm,
    invoke_llm,
    invoke_structured,
    require_safe_model,
)
from backend.app.models.evidence import PaperRecord, PRISMATracker, SearchProtocol
from backend.app.report_guard import (
    MIN_REPORT_WORDS,
    ReportGenerationError,
    deepsearch_report_is_valid,
    research_paper_is_valid,
    research_paper_text,
)
from backend.app.tools import academic_search, tavily_search
from backend.app.tools.academic_search import (
    SCREEN_BATCH_TIMEOUT_SECONDS,
    SCREEN_MIN_INCLUDED,
    SCREEN_TOP_UP_FLOOR,
    _expand_keywords_for_simple_sources,
    _format_picoc,
    _is_protocol_without_results,
    _score_batch,
    _screen_batch_timeout_seconds,
    _select_candidates,
    _simple_queries_for_keyword,
    format_apa,
    screen_papers_structured,
)

CITATION = "https://example.com/test"


def _body(words: int) -> str:
    return " ".join(["analysis"] * words)


def _paper(index: int, source: str = "openalex", **overrides) -> PaperRecord:
    data = {
        "paper_id": f"paper-{index}",
        "title": f"Study {index} of smartphone CBT for adult depression",
        "abstract": "A randomized evaluation of depressive symptoms.",
        "retrieval_source": source,
        "citation_count": index,
    }
    data.update(overrides)
    return PaperRecord(**data)


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content


def _scored_batch(scores: dict[str, float]):
    async def fake_score_batch(_llm, batch, *_args, **_kwargs):
        scored = []
        for paper in batch:
            copy = paper.model_copy(deep=True)
            copy.relevance_score = scores.get(paper.paper_id, 2.0)
            scored.append(copy)
        return scored

    return fake_score_batch


async def _screen(papers, scores, monkeypatch, problem="smartphone CBT for adult depression"):
    monkeypatch.setattr(academic_search, "get_llm", lambda **_kwargs: object())
    monkeypatch.setattr(academic_search, "_score_batch", _scored_batch(scores))
    return await screen_papers_structured(papers, problem, ["Reduce depressive symptoms"])


# ---------------------------------------------------------------------------
# 1. Report guard: junk output is rejected, a real report is accepted
# ---------------------------------------------------------------------------


def test_a_real_report_with_its_citation_is_valid():
    report = "# Findings\n\n" + _body(MIN_REPORT_WORDS)
    assert deepsearch_report_is_valid(
        report + f"\n\n## Sources\n\n- {CITATION}",
        citations=[{"url": CITATION}],
    )


def test_empty_and_short_reports_are_rejected():
    assert deepsearch_report_is_valid("", citations=[]) is False
    assert deepsearch_report_is_valid("User Safety: safe", citations=[]) is False
    assert (
        deepsearch_report_is_valid(
            "# Title\n\n" + _body(MIN_REPORT_WORDS - 2),
            citations=[],
        )
        is False
    )


def test_headings_without_section_text_are_rejected():
    assert (
        deepsearch_report_is_valid(
            "# Title\n\n## Overview\n\n## Findings\n\n## Sources & References",
            citations=[],
        )
        is False
    )


def test_retrieved_sources_must_appear_in_the_report():
    report = "# Report\n\n## Overview\n\n" + _body(MIN_REPORT_WORDS)
    assert deepsearch_report_is_valid(report, citations=[CITATION]) is False
    assert deepsearch_report_is_valid(
        report + f"\n\n## Sources\n\n- {CITATION}",
        citations=[CITATION],
    )


def test_research_paper_needs_text_and_references():
    values = {
        "abstract": _body(10),
        "introduction": _body(10),
        "references": [{"title": "Test reference"}],
    }
    assert research_paper_text(values).count("analysis") == 20
    assert research_paper_is_valid(values) is True


def test_research_paper_without_text_or_references_is_rejected():
    assert research_paper_is_valid({"references": [{"title": "Test"}]}) is False
    assert research_paper_is_valid({"abstract": _body(40), "references": []}) is False
    assert research_paper_is_valid({"abstract": _body(40), "references": None}) is False


def test_report_generation_error_message_is_user_safe():
    error = ReportGenerationError("internal detail")
    assert "internal detail" not in str(error)
    assert error.reason == "internal detail"


# ---------------------------------------------------------------------------
# 2. Bounded LLM retries and unsafe model ids
# ---------------------------------------------------------------------------


class _StatusError(Exception):
    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status_code = status


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(llm_module, "_retry_delay", lambda attempt: 0.0)
    llm_module._structured_method_cache.clear()
    yield
    llm_module._structured_method_cache.clear()


class _SyncLLM:
    def __init__(self, errors: list[BaseException]) -> None:
        self.errors = errors
        self.calls = 0

    def invoke(self, *_args, **_kwargs) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


class _AsyncLLM:
    def __init__(self, errors: list[BaseException]) -> None:
        self.errors = errors
        self.calls = 0

    async def ainvoke(self, *_args, **_kwargs) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


class _StructuredLLM:
    def __init__(self, failures: int, error: BaseException) -> None:
        self.failures = failures
        self.error = error
        self.calls = 0
        self.model_name = "retry-test-model"
        self.openai_api_base = "https://example.invalid/api/v1"

    def with_structured_output(self, _schema, method=None):
        return self

    def invoke(self, _prompt):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return {"ok": True}


def test_sync_invoke_retries_a_429_then_succeeds():
    fake = _SyncLLM([_StatusError("rate limited", 429)])
    assert invoke_llm(fake, "prompt") == "ok"
    assert fake.calls == 2


def test_sync_invoke_retries_a_404_provider_error_then_succeeds():
    fake = _SyncLLM([_StatusError("provider returned error", 404)])
    assert invoke_llm(fake, "prompt") == "ok"
    assert fake.calls == 2


def test_sync_invoke_stops_after_three_attempts():
    fake = _SyncLLM([_StatusError("rate limited", 429) for _ in range(3)])
    with pytest.raises(_StatusError):
        invoke_llm(fake, "prompt")
    assert fake.calls == 3


def test_sync_invoke_does_not_retry_an_auth_error():
    fake = _SyncLLM([_StatusError("invalid api key", 401)])
    with pytest.raises(_StatusError):
        invoke_llm(fake, "prompt")
    assert fake.calls == 1


def test_sync_invoke_retries_a_rate_limit_message_without_a_status():
    fake = _SyncLLM([RuntimeError("provider is rate limited, try later")])
    assert invoke_llm(fake, "prompt") == "ok"
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_async_invoke_retries_a_429_then_succeeds():
    fake = _AsyncLLM([_StatusError("rate limited", 429)])
    assert await ainvoke_llm(fake, "prompt") == "ok"
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_async_invoke_stops_after_three_attempts():
    fake = _AsyncLLM([_StatusError("provider returned error", 404) for _ in range(3)])
    with pytest.raises(_StatusError):
        await ainvoke_llm(fake, "prompt")
    assert fake.calls == 3


def test_structured_output_retries_transient_failures():
    fake = _StructuredLLM(
        failures=len(llm_module.STRUCTURED_METHODS),
        error=_StatusError("rate limited", 429),
    )
    assert invoke_structured(fake, dict, "prompt") == {"ok": True}
    assert fake.calls == len(llm_module.STRUCTURED_METHODS) + 1


def test_forbidden_model_ids_are_rejected():
    for model in ("openrouter/free", "vendor/safety-classifier", "acme/moderation-guard"):
        with pytest.raises(RuntimeError):
            require_safe_model(model)


def test_a_normal_completion_model_is_allowed():
    assert require_safe_model("deepseek-chat") == "deepseek-chat"


# ---------------------------------------------------------------------------
# 3. Tavily tolerates unset/invalid optional arguments
# ---------------------------------------------------------------------------


@pytest.fixture()
def tavily_calls(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {}

    def fake_search(**kwargs):
        captured.update(kwargs)
        return {"results": []}

    monkeypatch.setattr(tavily_search.tavily_client, "search", fake_search)
    return captured


@pytest.mark.parametrize("raw", ["none", "null", "", None])
def test_unset_values_are_not_sent_to_tavily(tavily_calls, raw):
    result = tavily_search.search_web.invoke(
        {"query": "quantum", "search_topic": raw, "time_range": raw}
    )
    assert "Error using Tavily search" not in result
    assert tavily_calls["query"] == "quantum"
    assert tavily_calls["time_range"] is None


def test_a_single_topic_string_becomes_a_one_item_list(tavily_calls):
    tavily_search.search_web.invoke({"query": "quantum", "search_topic": "academic"})
    assert tavily_calls["query"] == "quantum Use sources: academic"


def test_a_topic_list_is_preserved_and_unset_entries_are_dropped(tavily_calls):
    tavily_search.search_web.invoke(
        {"query": "quantum", "search_topic": ["news", "none", "academic"]}
    )
    assert tavily_calls["query"] == "quantum Use sources: news, academic"


@pytest.mark.parametrize("value", ["day", "week", "month", "year"])
def test_valid_time_ranges_are_forwarded(tavily_calls, value):
    tavily_search.search_web.invoke({"query": "quantum", "time_range": value})
    assert tavily_calls["time_range"] == value


def test_an_unknown_time_range_is_treated_as_unset(tavily_calls):
    tavily_search.search_web.invoke({"query": "quantum", "time_range": "last week"})
    assert tavily_calls["time_range"] is None


# ---------------------------------------------------------------------------
# 4. Screening: timeouts, calibration, inclusion and PRISMA counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timed_out_batch_is_retried_split_and_never_scores_neutral(monkeypatch):
    calls: list[str] = []

    async def fake_ainvoke(_llm, prompt):
        calls.append(prompt)
        if len(calls) == 1:
            raise TimeoutError()
        ids = [int(match) for match in re.findall(r"\[(\d+)\] Title:", prompt)]
        return _Response(json.dumps([{"id": paper_id, "score": 8} for paper_id in ids]))

    monkeypatch.setattr(academic_search, "ainvoke_llm", fake_ainvoke)

    batch = [_paper(index) for index in range(15)]
    scored = await _score_batch(
        object(),
        batch,
        "smartphone CBT for adult depression",
        "- Reduce depressive symptoms",
        asyncio.Semaphore(2),
        "",
        timeout=1.0,
    )

    assert len(calls) == 3  # original batch + two half-batch retries
    assert [paper.relevance_score for paper in scored] == [8.0] * 15
    assert all(paper.relevance_score != 5 for paper in scored)


@pytest.mark.asyncio
async def test_permanently_failed_batch_is_marked_unscored_not_neutral(monkeypatch):
    async def always_timeout(_llm, _prompt):
        raise TimeoutError()

    monkeypatch.setattr(academic_search, "ainvoke_llm", always_timeout)

    batch = [_paper(index) for index in range(4)]
    scored = await _score_batch(
        object(),
        batch,
        "smartphone CBT",
        "- objective",
        asyncio.Semaphore(1),
        "",
        timeout=1.0,
    )

    assert len(scored) == 4
    assert all(paper.relevance_score is None for paper in scored)


def test_batch_timeout_reads_env_override(monkeypatch):
    assert _screen_batch_timeout_seconds() == SCREEN_BATCH_TIMEOUT_SECONDS

    monkeypatch.setenv("SCREEN_BATCH_TIMEOUT_SECONDS", "120")
    assert _screen_batch_timeout_seconds() == 120.0

    monkeypatch.setenv("SCREEN_BATCH_TIMEOUT_SECONDS", "not-a-number")
    assert _screen_batch_timeout_seconds() == SCREEN_BATCH_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_screening_fails_when_more_than_30_percent_stay_unscored(monkeypatch):
    papers = [_paper(index) for index in range(40)]

    async def half_weighted(_llm, batch, *_args, **_kwargs):
        scored = []
        for offset, paper in enumerate(batch):
            copy = paper.model_copy(deep=True)
            copy.relevance_score = 9.0 if offset % 2 == 0 else None
            scored.append(copy)
        return scored

    monkeypatch.setattr(academic_search, "get_llm", lambda **_kwargs: object())
    monkeypatch.setattr(academic_search, "_score_batch", half_weighted)

    with pytest.raises(ReportGenerationError):
        await screen_papers_structured(papers, "smartphone CBT", ["objective"])


@pytest.mark.asyncio
async def test_screening_keeps_going_when_unscored_share_is_acceptable(monkeypatch):
    papers = [_paper(index) for index in range(40)]

    async def quarter_unscored(_llm, batch, *_args, **_kwargs):
        scored = []
        for paper in batch:
            copy = paper.model_copy(deep=True)
            index = int(paper.paper_id.rsplit("-", 1)[1])
            copy.relevance_score = None if index % 4 == 0 else 9.0
            scored.append(copy)
        return scored

    monkeypatch.setattr(academic_search, "get_llm", lambda **_kwargs: object())
    monkeypatch.setattr(academic_search, "_score_batch", quarter_unscored)

    screened, tracker = await screen_papers_structured(papers, "smartphone CBT", ["objective"])

    included = [paper for paper in screened if paper.screening_status == "included"]
    assert len(included) == 30
    assert tracker.exclusion_reasons["Not scored: screening batch failure"] == 10
    assert tracker.validate_invariants() == []


@pytest.mark.asyncio
async def test_inclusion_takes_all_strong_scores_without_top_up(monkeypatch):
    papers = [_paper(index) for index in range(40)]
    scores = {paper.paper_id: 8.0 for paper in papers[:10]}
    scores.update({paper.paper_id: 6.0 for paper in papers[10:]})

    screened, tracker = await _screen(papers, scores, monkeypatch)

    included = [paper for paper in screened if paper.screening_status == "included"]
    assert len(included) == 10
    assert all(paper.relevance_score == 8.0 for paper in included)
    assert all("top-up" not in (paper.exclusion_reason or "") for paper in included)
    assert tracker.validate_invariants() == []


@pytest.mark.asyncio
async def test_inclusion_tops_up_to_minimum_with_papers_scoring_five_or_more(monkeypatch):
    papers = [_paper(index) for index in range(40)]
    scores = {paper.paper_id: 9.0 for paper in papers[:4]}
    scores.update({paper.paper_id: 6.0 for paper in papers[4:8]})

    screened, tracker = await _screen(papers, scores, monkeypatch)

    included = [paper for paper in screened if paper.screening_status == "included"]
    top_up = [paper for paper in included if "top-up" in (paper.exclusion_reason or "")]
    assert len(included) == SCREEN_MIN_INCLUDED
    assert len(top_up) == SCREEN_MIN_INCLUDED - 4
    assert all(paper.relevance_score >= SCREEN_TOP_UP_FLOOR for paper in top_up)
    assert tracker.studies_included == SCREEN_MIN_INCLUDED
    assert tracker.validate_invariants() == []


@pytest.mark.asyncio
async def test_inclusion_fails_the_run_when_fewer_than_three_reach_five(monkeypatch):
    papers = [_paper(index) for index in range(40)]
    scores = {paper.paper_id: 6.0 for paper in papers[:2]}

    monkeypatch.setattr(academic_search, "get_llm", lambda **_kwargs: object())
    monkeypatch.setattr(academic_search, "_score_batch", _scored_batch(scores))

    with pytest.raises(ReportGenerationError):
        await screen_papers_structured(papers, "smartphone CBT", ["objective"])


def test_boolean_keyword_splits_into_plain_queries():
    queries = _simple_queries_for_keyword(
        "Depression OR depressive disorder OR MDD OR major depressive disorder",
        limit=2,
    )
    assert queries == ["Depression", "depressive disorder"]
    assert all(not re.search(r"\b(?:AND|OR|NOT)\b|[()\"]", query) for query in queries)


def test_boolean_and_groups_keep_population_and_intervention_together():
    queries = _simple_queries_for_keyword(
        "(depression OR MDD) AND (smartphone app OR mHealth) "
        "AND (CBT OR cognitive behavioral therapy)",
        limit=3,
    )
    assert len(queries) == 3
    assert queries[0] == "depression smartphone app CBT"
    assert all(not re.search(r"\b(?:AND|OR|NOT)\b|[()\"]", query) for query in queries)


def test_keyword_expansion_is_capped_and_round_robin():
    expanded = _expand_keywords_for_simple_sources(
        ["alpha OR beta", "gamma OR delta", "plain query"],
        max_queries=3,
    )
    assert expanded == ["alpha", "gamma", "plain query"]


@pytest.mark.asyncio
async def test_search_sends_simple_queries_to_semantic_scholar_and_boolean_to_pubmed(monkeypatch):
    seen: dict[str, list[str]] = {
        "semantic_scholar": [],
        "openalex": [],
        "pubmed": [],
    }

    def adapter(name: str):
        async def fake(query, limit=15, **_kwargs):
            seen[name].append(query)
            return []

        return fake

    monkeypatch.setattr(academic_search, "search_semantic_scholar", adapter("semantic_scholar"))
    monkeypatch.setattr(academic_search, "search_openalex", adapter("openalex"))
    monkeypatch.setattr(academic_search, "search_pubmed", adapter("pubmed"))
    for name in ("search_europe_pmc", "search_doaj", "search_datacite", "search_crossref"):
        monkeypatch.setattr(academic_search, name, adapter("other"))
    monkeypatch.setattr(
        academic_search,
        "fetch_arxiv_papers",
        lambda _client, _kw, max_results=50: asyncio.sleep(0, result=[]),
    )
    monkeypatch.setattr(
        academic_search,
        "fetch_tavily_web_papers",
        lambda _kw, max_results=5: asyncio.sleep(0, result=[]),
    )

    boolean_keyword = "Depression OR depressive disorder OR MDD"
    await academic_search.search_academic_papers_structured([boolean_keyword])

    assert seen["semantic_scholar"]
    assert all(
        not re.search(r"\b(?:AND|OR|NOT)\b|[()\"]", query)
        for query in seen["semantic_scholar"]
    )
    assert seen["pubmed"] == [boolean_keyword]


def test_select_candidates_prefers_title_overlap_within_a_source():
    papers = [
        _paper(0, title="Unrelated Cooking Techniques", abstract="Pastry survey."),
        _paper(1, title="Smartphone CBT for Adult Depression", abstract="Randomized trial."),
    ]

    candidates = _select_candidates(
        papers,
        limit=2,
        problem_statement="smartphone CBT for adult depression",
        objectives=["reduce depressive symptoms"],
    )

    assert [paper.paper_id for paper in candidates] == ["paper-1", "paper-0"]


def test_protocol_detection_ignores_protocols_that_report_results():
    protocol = PaperRecord(
        paper_id="proto",
        title="Protocol for a Randomized Trial of Cooling",
        abstract="We will enrol 500 patients and measure mortality.",
    )
    completed = PaperRecord(
        paper_id="done",
        title="Protocol for a Randomized Trial of Cooling",
        abstract="Results: mortality was lower in the cooling arm (p = 0.01).",
    )
    assert _is_protocol_without_results(protocol) is True
    assert _is_protocol_without_results(completed) is False


def test_format_picoc_renders_scope_and_criteria():
    protocol = SearchProtocol(
        population="Adults with sepsis",
        intervention="Targeted temperature management",
        comparison="Standard care",
        context="Intensive care units",
        outcomes=["Mortality", "Neurological outcome"],
        inclusion_criteria=["Randomized trials"],
        exclusion_criteria=["Animal studies"],
    )

    rendered = _format_picoc(protocol)

    assert "Population: Adults with sepsis" in rendered
    assert "Outcomes: Mortality; Neurological outcome" in rendered
    assert "Inclusion criteria:" in rendered
    assert "Animal studies" in rendered


@pytest.mark.asyncio
async def test_screening_excludes_protocols_and_papers_below_the_relevance_floor(monkeypatch):
    relevant = [
        PaperRecord(
            paper_id=f"rel-{index}",
            title=f"Relevant Empirical Study {index}",
            abstract="We found a strong improvement in the primary outcome.",
            retrieval_source="openalex",
            citation_count=index,
        )
        for index in range(30)
    ]
    off_topic = PaperRecord(
        paper_id="off-topic",
        title="Unrelated Cooking Study",
        abstract="A survey of pastry techniques.",
        retrieval_source="crossref",
        citation_count=999,
    )
    protocol = PaperRecord(
        paper_id="protocol",
        title="Protocol for a Randomized Trial of Robustness",
        abstract="We will enrol participants and analyse the primary outcome.",
        retrieval_source="pubmed",
        citation_count=500,
    )
    papers = relevant + [off_topic, protocol]
    assert len(papers) > academic_search.SCREEN_THRESHOLD

    monkeypatch.setattr(academic_search, "get_llm", lambda **_kwargs: object())

    async def fake_score_batch(_llm, batch, *_args, **_kwargs):
        scored = []
        for paper in batch:
            copy = paper.model_copy(deep=True)
            copy.relevance_score = 2.0 if copy.title == "Unrelated Cooking Study" else 9.0
            scored.append(copy)
        return scored

    monkeypatch.setattr(academic_search, "_score_batch", fake_score_batch)

    screened, tracker = await academic_search.screen_papers_structured(
        papers,
        "Robustness of deep networks",
        ["Measure robustness under distribution shift"],
        protocol=SearchProtocol(population="Deep networks"),
    )

    included = [paper for paper in screened if paper.screening_status == "included"]
    assert len(included) == 30
    assert all((paper.relevance_score or 0) >= academic_search.SCREEN_MIN_RELEVANCE for paper in included)
    assert all(paper.title != "Unrelated Cooking Study" for paper in included)

    excluded_protocol = next(paper for paper in screened if paper.paper_id == "protocol")
    assert excluded_protocol.screening_status == "excluded"
    assert excluded_protocol.exclusion_reason == "Protocol without reported results"
    assert tracker.exclusion_reasons["Protocol without reported results"] == 1
    assert tracker.exclusion_reasons["Title/abstract irrelevance"] == 1
    assert tracker.validate_invariants() == []


@pytest.mark.asyncio
async def test_screening_reconciles_tracker_with_the_actual_corpus():
    papers = [
        PaperRecord(paper_id=f"p-{index}", title=f"Paper {index}")
        for index in range(5)
    ]
    tracker = PRISMATracker(
        records_identified=5,
        duplicates_removed=2,
        records_after_dedup=3,
    )

    _, result = await academic_search.screen_papers_structured(
        papers,
        "Robustness of deep networks",
        ["Measure robustness"],
        tracker=tracker,
    )

    assert result.records_after_dedup == 5
    assert result.records_identified == 7
    assert result.validate_invariants() == []


@pytest.mark.asyncio
async def test_citation_expander_counts_new_records_in_prisma(monkeypatch):
    async def fake_expand(_seed_dois, max_expansion=15):
        return ["10.9999/new"]

    async def fake_resolve(_dois):
        return [
            PaperRecord(
                paper_id="new-paper",
                title="Newly Connected Paper",
                doi="10.9999/new",
                retrieval_source="opencitations",
            )
        ]

    monkeypatch.setattr(retrieval, "expand_citation_graph", fake_expand)
    monkeypatch.setattr(retrieval, "_resolve_dois_via_crossref", fake_resolve)

    state = {
        "paper_records": [
            {"paper_id": "seed", "title": "Seed", "doi": "10.1234/seed"}
        ],
        "prisma_tracker": PRISMATracker(
            records_identified=3,
            duplicates_removed=1,
            records_after_dedup=2,
            records_by_source={"opencitations": 0},
        ).model_dump(),
    }

    updates = await retrieval.citation_expander_agent(state)
    tracker = PRISMATracker(**updates["prisma_tracker"])

    assert len(updates["paper_records"]) == 2
    assert tracker.records_identified == 4
    assert tracker.records_after_dedup == 3
    assert tracker.records_by_source["opencitations"] == 1
    assert tracker.validate_invariants() == []
    assert updates["corpus_stats"]["after_dedup"] == 3


# ---------------------------------------------------------------------------
# 5. Research Mode paper quality
# ---------------------------------------------------------------------------


def test_normalize_section_body_removes_title_and_demotes_headings():
    body = "## Literature Review\n\n### Theme One\n\nEvidence converges."
    assert _common.normalize_section_body(body, section_title="Literature Review") == (
        "### Theme One\n\nEvidence converges."
    )


def test_strip_abstract_heading_removes_a_leading_abstract_label():
    assert _common.strip_abstract_heading("## Abstract\n\nA short summary.") == "A short summary."


@pytest.mark.asyncio
async def test_safe_invoke_llm_logs_exception_type_only(monkeypatch, caplog):
    class Boom:
        calls = 0

        def invoke(self, *_args, **_kwargs):
            Boom.calls += 1
            raise RuntimeError("503 secret prompt body sk-live-should-not-appear")

    with caplog.at_level(logging.WARNING):
        result = await _common._safe_invoke_llm(
            Boom(), [], max_retries=0, node="literature_review"
        )

    # One attempt per outer try: no nested transient retries.
    assert Boom.calls == 1

    assert result == ""
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "RuntimeError" in log_text
    assert "literature_review" in log_text
    assert "secret prompt body" not in log_text
    assert "sk-live" not in log_text


@pytest.mark.asyncio
async def test_write_required_section_retries_once_with_compact_prompt():
    prompts = []

    async def fake_invoke(_llm, messages, **_kwargs):
        prompts.append(messages[0].content)
        if len(prompts) == 1:
            return ""
        return "## Literature Review\n\nThe evidence converges on one mechanism."

    body = await _common.write_required_section(
        fake_invoke,
        object(),
        "full prompt",
        section="Literature Review",
        compact_prompt="compact prompt",
    )

    assert prompts == ["full prompt", "compact prompt"]
    assert body == "The evidence converges on one mechanism."


@pytest.mark.asyncio
async def test_write_required_section_fails_instead_of_storing_empty():
    calls = []

    async def always_empty(_llm, _messages, **_kwargs):
        calls.append(1)
        return ""

    with pytest.raises(ReportGenerationError):
        await _common.write_required_section(
            always_empty,
            object(),
            "full prompt",
            section="Conceptual Framework",
            compact_prompt="compact prompt",
        )

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_literature_review_agent_never_returns_empty_section(monkeypatch):
    monkeypatch.setattr(writing, "get_llm_for", lambda *_a, **_k: object())

    async def always_empty(_llm, _messages, **_kwargs):
        return ""

    monkeypatch.setattr(writing, "_safe_invoke_llm", always_empty)

    with pytest.raises(ReportGenerationError):
        await writing.literature_review_agent(
            {"problem_statement": "Robustness of deep networks"}
        )


def _paper_dict(paper_id, authors, year, title, **overrides):
    record = {
        "paper_id": paper_id,
        "authors": authors,
        "year": year,
        "title": title,
        "screening_status": "included",
    }
    record.update(overrides)
    return record


@pytest.mark.asyncio
async def test_claims_linker_renders_apa_citations_and_prunes_uncited_references():
    cited = _paper_dict("p-cited", ["Curie, M."], "2021", "Alpha Paper")
    uncited = _paper_dict("p-uncited", ["Noether, E."], "2019", "Uncited Paper")
    evidence = [
        {
            "evidence_id": "p-cited_ev001",
            "paper_id": "p-cited",
            "claim_summary": "Model reaches 94.2% accuracy.",
        }
    ]
    state = {
        "paper_records": [cited, uncited],
        "evidence_records": evidence,
        "references": [format_apa(cited), format_apa(uncited)],
        "introduction": (
            "Prior work reported strong benchmark gains "
            "[EV:p-cited_ev001]. A fabricated tag [EV:ghost_ev999] is dropped."
        ),
    }

    result = await writing.claims_linker_node(state)

    assert "(Curie, 2021)" in result["introduction"]
    assert "[EV:" not in result["introduction"]
    assert "ghost_ev999" not in result["introduction"]
    assert result["review_claims"][0]["supporting_evidence_ids"] == ["p-cited_ev001"]
    assert result["references"] == [format_apa(cited)]
    assert all("Uncited Paper" not in ref for ref in result["references"])


@pytest.mark.asyncio
async def test_implications_agent_writes_substantive_prose(monkeypatch):
    monkeypatch.setattr(writing, "get_llm_for", lambda *_a, **_k: object())

    async def fake_invoke(_llm, _messages, **_kwargs):
        return (
            "The evidence qualifies the scaling hypothesis.\n\n"
            "### Theoretical Implications\n\n"
            "The synthesis narrows the scope of the scaling claim.\n\n"
            "### Practical Implications\n\n"
            "Teams should benchmark under distribution shift."
        )

    monkeypatch.setattr(writing, "_safe_invoke_llm", fake_invoke)

    result = await writing.implications_agent(
        {
            "problem_statement": "Robustness of deep networks",
            "results": "Accuracy improves with scale [EV:p_ev001].",
            "discussion": "Scale does not explain robustness [EV:p_ev001].",
            "evidence_records": [
                {
                    "evidence_id": "p_ev001",
                    "paper_id": "p",
                    "claim_summary": "Accuracy improves.",
                }
            ],
        }
    )

    body = result["implications"]
    assert "Theoretical Implications" in body
    assert "Practical Implications" in body
    assert "discussed in the Discussion section" not in body


def test_implications_node_is_wired_between_discussion_and_limitations():
    from backend.app.graph import research_mode_builder as builder_mod

    assert "implications" in builder_mod.builder.nodes
    edges = set(builder_mod.builder.edges)
    assert ("discussion", "implications") in edges
    assert ("implications", "limitations") in edges


def test_evidence_context_is_not_capped_by_default():
    records = [
        {
            "evidence_id": f"p_ev{index:03d}",
            "paper_id": "p",
            "claim_summary": f"Finding number {index}.",
        }
        for index in range(20)
    ]

    full = writing._format_evidence_context({"evidence_records": records})
    assert full.count("Evidence ID:") == 20

    capped = writing._format_evidence_context({"evidence_records": records}, max_items=3)
    assert capped.count("Evidence ID:") == 3


def test_title_sanitizer_removes_meta_analysis_claims():
    sanitized = writing._sanitize_title(
        "Deep Learning in Healthcare: A Systematic Review and Meta-Analysis",
        fallback="Fallback",
    )
    assert "meta" not in sanitized.lower()
    assert sanitized == "Deep Learning in Healthcare: A Systematic Review"

    assert "meta" not in writing._sanitize_title("A Meta-Analytic Review of X", "F").lower()
    assert writing._sanitize_title("", "Fallback Title") == "Fallback Title"


@pytest.mark.asyncio
async def test_title_agent_sanitizes_model_output(monkeypatch):
    monkeypatch.setattr(writing, "get_llm_for", lambda *_a, **_k: object())

    async def fake_invoke(_llm, _messages, **_kwargs):
        return "A Meta-Analysis of Robustness in Deep Networks."

    monkeypatch.setattr(writing, "_safe_invoke_llm", fake_invoke)

    result = await writing.title_agent({"problem_statement": "Robustness"})

    assert "meta" not in result["title"].lower()


def test_results_strip_bare_hypothesis_restatements_but_keep_findings():
    body = (
        "H1: Data scale is positively associated with robustness.\n\n"
        "**H2:** Larger models are more robust.\n\n"
        "H3: Evidence supports the hypothesis that scale improves robustness.\n\n"
        "The pooled direction across studies is positive."
    )

    cleaned = writing._strip_hypothesis_restatements(body)

    assert "H1:" not in cleaned
    assert "Larger models are more robust" not in cleaned
    assert "Evidence supports the hypothesis" in cleaned
    assert "The pooled direction across studies is positive." in cleaned


@pytest.mark.asyncio
async def test_gap_analysis_writes_research_gap_into_the_paper(monkeypatch):
    async def fake_invoke(_llm, _messages, **_kwargs):
        return (
            "### 1. Methodological Gaps\n\nTrials are short, unblinded, and rarely report "
            "dropout, so the durability of any symptom improvement remains untested."
        )

    monkeypatch.setattr(synthesis, "get_llm_for", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(synthesis, "_safe_invoke_llm", fake_invoke)

    result = await synthesis.gap_analysis_agent({
        "problem_statement": "smartphone CBT for adult depression",
        "evidence_records": [{"claim_summary": "CBT reduced symptoms", "limitations": "short follow-up"}],
    })

    gap = result["research_gap"]
    assert gap.strip()
    assert "1. Methodological Gaps" in gap

    values = {"research_gap": gap, "references": ["Author (2020). A trial."]}
    assert research_paper_is_valid(values)
    assert gap in research_paper_text(values)


@pytest.mark.asyncio
async def test_gap_analysis_fails_instead_of_dropping_the_section(monkeypatch):
    async def always_empty(_llm, _messages, **_kwargs):
        return ""

    monkeypatch.setattr(synthesis, "get_llm_for", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(synthesis, "_safe_invoke_llm", always_empty)

    with pytest.raises(ReportGenerationError):
        await synthesis.gap_analysis_agent({
            "problem_statement": "smartphone CBT for adult depression",
            "evidence_records": [],
        })


def test_format_apa_cleans_html_entities_casing_and_punctuation():
    paper = {
        "title": "<i>Attention</i> &amp; Scale.",
        "authors": ["  john   a. powell", "chen sj"],
        "year": "<b>2020</b>",
        "doi": "doi: 10.48550/arXiv.1706.03762",
        "venue": "Journal <i>of</i> Things.",
    }

    apa = format_apa(paper)

    assert "John A. Powell & Chen Sj (2020)." in apa
    assert "Attention & Scale." in apa
    assert "<i>" not in apa
    assert "&amp;" not in apa
    assert "Journal of Things." in apa
    assert "https://doi.org/10.48550/arXiv.1706.03762" in apa
    assert ".." not in apa
    assert " ." not in apa


class _FakeGraphState:
    def __init__(self, values):
        self.values = values
        self.next = ()
        self.tasks = ()


class _FakeGraph:
    def __init__(self, values):
        self._state = _FakeGraphState(values)

    async def aget_state(self, _config):
        return self._state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("values", "rejected"),
    [
        ({"status": "completed", "references": []}, True),
        (
            {"status": "completed", "abstract": _body(40), "references": [{"title": "T"}]},
            False,
        ),
    ],
)
async def test_result_endpoint_applies_report_guard(monkeypatch, values, rejected):
    from backend.app.api import research_mode as api

    monkeypatch.setattr(api, "get_research_mode_graph", lambda: _FakeGraph(values))
    monkeypatch.setattr(api, "_resolve_checkpoint_state", lambda _state: (False, None))

    result = await api.get_research_mode_result("thread-1")

    assert result["report_rejected"] is rejected
    assert result["is_completed"] is (not rejected)
    assert ("message" in result) is rejected


@pytest.mark.asyncio
async def test_evidence_extractor_skips_screening_exclusions(monkeypatch):
    from backend.app.agents.research_mode import extraction

    abstract = "A randomised trial found that the app reduced depressive symptoms at twelve weeks."
    papers = [
        {"paper_id": "inc1", "title": "Included trial", "abstract": abstract,
         "screening_status": "included"},
        {"paper_id": "prot1", "title": "Trial protocol", "abstract": abstract,
         "screening_status": "excluded", "exclusion_reason": "Protocol without reported results"},
    ]

    async def no_items(_llm, _messages, **_kwargs):
        return "[]"

    monkeypatch.setattr(extraction, "get_llm_for", lambda *_a, **_k: object())
    monkeypatch.setattr(extraction, "_safe_invoke_llm", no_items)

    result = await extraction.evidence_extractor_agent({"paper_records": papers})

    assert {e["paper_id"] for e in result["evidence_records"]} == {"inc1"}
    # The full audit list is still returned for PRISMA reporting.
    assert [p["paper_id"] for p in result["paper_records"]] == ["inc1", "prot1"]


@pytest.mark.asyncio
async def test_retracted_openalex_record_is_excluded_at_screening():
    import httpx

    from backend.app.tools.openalex_search import search_openalex

    payload = {"results": [{
        "doi": "https://doi.org/10.1/retracted",
        "title": "A retracted trial",
        "publication_year": 2020,
        "authorships": [{"author": {"display_name": "Ann Smith"}}],
        "is_retracted": True,
    }]}
    transport = httpx.MockTransport(lambda _req: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        records = await search_openalex("trial", client=client)

    assert records[0].is_retracted is True
    # The flag must survive the dict round trip the pipeline state uses.
    restored = PaperRecord.from_dict(records[0].model_dump())
    assert restored.is_retracted is True

    screened, tracker = await screen_papers_structured([restored], "trial", [])
    assert screened[0].screening_status == "excluded"
    assert tracker.exclusion_reasons.get("Retracted publication") == 1


def _linker_fixture():
    cited = _paper_dict("p-cited", ["Curie, M."], "2021", "Alpha Paper")
    evidence = {"p-cited_ev001": {"evidence_id": "p-cited_ev001", "paper_id": "p-cited"}}
    return cited, evidence


def test_marker_rendering_keeps_line_and_paragraph_breaks():
    cited, evidence = _linker_fixture()
    papers = {"p-cited": cited}

    def render(text):
        # Same two steps claims_linker_node applies to each section.
        return writing._clean_citation_punctuation(
            writing._render_markers_as_citations(text, evidence, papers)
        )

    assert render("Strong gains [EV:p-cited_ev001]\n\nNext paragraph.") == (
        "Strong gains (Curie, 2021)\n\nNext paragraph."
    )
    # A dropped (unresolvable) marker must not glue two sentences together.
    assert render("X.[EV:ghost_ev9] Next") == "X. Next"


@pytest.mark.asyncio
async def test_claims_linker_defers_marker_on_its_own_line():
    cited, evidence = _linker_fixture()
    state = {
        "paper_records": [cited],
        "evidence_records": list(evidence.values()),
        "references": [format_apa(cited)],
        # The trailing marker sits on its own line, so it splits off as its own
        # "sentence" and renders to a bare "(Curie, 2021)".
        "results": "Gains were reported [EV:p-cited_ev001]. Accuracy rose sharply.\n[EV:p-cited_ev001]",
    }

    result = await writing.claims_linker_node(state)

    claim_texts = [c["claim_text"] for c in result["review_claims"]]
    assert claim_texts == ["Gains were reported (Curie, 2021)."]


@pytest.mark.asyncio
async def test_claims_linker_keeps_reference_cited_without_marker():
    cited, evidence = _linker_fixture()
    narrative = _paper_dict("p-narr", ["Lovelace, A.", "Babbage, C.", "Turing, A."], "2018", "Narr")
    uncited = _paper_dict("p-uncited", ["Noether, E."], "2019", "Uncited Paper")
    state = {
        "paper_records": [cited, narrative, uncited],
        "evidence_records": list(evidence.values()),
        "references": [format_apa(cited), format_apa(narrative), format_apa(uncited)],
        "introduction": (
            "Prior work reported gains [EV:p-cited_ev001]. "
            "Lovelace et al. (2018) framed the problem."
        ),
    }

    result = await writing.claims_linker_node(state)

    assert format_apa(narrative) in result["references"]
    assert format_apa(cited) in result["references"]
    assert format_apa(uncited) not in result["references"]


@pytest.mark.parametrize(
    "title",
    ["Meta-Analytic Review of X", "A Meta-Analysis Review of X", "Systematic Review and Meta-Analysis of X"],
)
def test_sanitize_title_never_doubles_review(title):
    cleaned = writing._sanitize_title(title, "F")
    assert "Review Review" not in cleaned
    assert "meta" not in cleaned.lower()
    assert "Systematic Review" in cleaned


@pytest.mark.asyncio
async def test_implications_prompts_request_evidence_markers(monkeypatch):
    prompts = []
    monkeypatch.setattr(writing, "get_llm_for", lambda *_a, **_k: object())

    async def capture(_llm, messages, **_kwargs):
        prompts.append(messages[0].content)
        return "" if len(prompts) == 1 else "Implications grounded in evidence. " * 20

    monkeypatch.setattr(writing, "_safe_invoke_llm", capture)
    await writing.implications_agent({"problem_statement": "Topic"})

    assert len(prompts) == 2  # full prompt, then the compact retry
    assert all("[EV:<Evidence ID>]" in prompt for prompt in prompts)


@pytest.mark.asyncio
async def test_writers_offer_only_included_papers_for_citation(monkeypatch):
    included = _paper_dict("p-in", ["Curie, M."], "2021", "Included Trial")
    excluded = _paper_dict("p-out", ["Noether, E."], "2019", "Retracted Trial")
    excluded["screening_status"] = "excluded"
    state = {"problem_statement": "Topic", "paper_records": [included, excluded]}

    prompts = []
    monkeypatch.setattr(writing, "get_llm_for", lambda *_a, **_k: object())

    async def capture(_llm, messages, **_kwargs):
        prompts.append(messages[0].content)
        return "Grounded body text. " * 30

    monkeypatch.setattr(writing, "_safe_invoke_llm", capture)
    await writing.implications_agent(state)
    await writing.literature_review_agent(state)

    implications, literature = prompts[0], prompts[1]
    assert "(Curie, 2021)" in implications
    assert "Noether" not in implications
    assert "Included Trial" in literature
    assert "Retracted Trial" not in literature
