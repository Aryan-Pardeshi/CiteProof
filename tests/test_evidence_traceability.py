"""Traceability tests for the Academic Evidence Layer.

Gate contract under test:
  Claim -> EvidenceSpan -> Paper -> exact location (section+page) -> source URL/DOI

Every surfaced claim must carry a non-null paper_id, section, and source_url,
an evidence span anchored to the paper text, and an explicit confidence score
derived from the documented extraction-method rule (never arbitrary).
"""

import json

import pytest
from pydantic import ValidationError

from backend.app.models.evidence import (
    Claim,
    ConfidenceBasis,
    EvidenceSpan,
    build_claims,
    build_evidence_chains,
    confidence_label,
    locate_quote,
    make_paper_id,
    score_confidence,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PAGE_ONE = "Introduction. Deep architectures dominate modern perception tasks."
PAGE_TWO = "Results. The proposed model reaches a top-1 accuracy of 94.2 percent on ImageNet."
FULLTEXT = PAGE_ONE + "\f" + PAGE_TWO

ABSTRACT = "We survey robustness of deep networks against distribution shift."

PAPER = {
    "paper_id": make_paper_id(doi="10.1234/traced", title="Traced Paper"),
    "title": "Traced Paper",
    "doi": "10.1234/traced",
    "source_url": "https://example.org/traced",
    "abstract": ABSTRACT,
    "fulltext_excerpt": FULLTEXT,
    "retrieval_source": "openalex",
}


def _canned_llm_response(items):
    async def _fake(llm, messages):
        return json.dumps(items)

    return _fake

def _fake_llm():
    return object()


# ---------------------------------------------------------------------------
# 1. Data models: Claim always resolves paper_id + section + source_url
# ---------------------------------------------------------------------------


def test_claim_requires_paper_id_and_span():
    """A claim without paper_id or evidence_span_id must not be constructible."""
    with pytest.raises(ValidationError):
        Claim(text="Model X beats baseline", paper_id=None, evidence_span_id="s1")
    with pytest.raises(ValidationError):
        Claim(text="Model X beats baseline", evidence_span_id="s1")
    with pytest.raises(ValidationError):
        Claim(text="Model X beats baseline", paper_id="p1")


def test_claim_section_and_source_url_never_null():
    """section falls back to explicit 'unknown' sentinel; source_url is never None."""
    claim = Claim(text="Model X beats baseline", paper_id="p1", evidence_span_id="p1_sp001")
    assert claim.section is not None and claim.section == "unknown"
    assert claim.source_url is not None
    assert claim.page is None  # unknown stays explicitly null, never guessed


def test_evidence_span_offsets_and_page():
    span = EvidenceSpan(
        span_id="p1_sp001",
        paper_id="p1",
        text=PAGE_TWO.strip(),
        section="Results",
        page=2,
        char_offset_start=len(PAGE_ONE) + 1,
        char_offset_end=len(PAGE_ONE) + 1 + len(PAGE_TWO),
    )
    assert span.page == 2
    d = span.model_dump()
    assert d["char_offset_end"] > d["char_offset_start"]


# ---------------------------------------------------------------------------
# 2. Explicit confidence scoring rule
# ---------------------------------------------------------------------------


def test_confidence_rule_exact_fulltext_is_high():
    base = score_confidence(ConfidenceBasis.EXACT_QUOTE_FULLTEXT)
    assert base == 0.9


def test_confidence_rule_orders_extraction_methods():
    full = score_confidence(ConfidenceBasis.EXACT_QUOTE_FULLTEXT)
    abstract = score_confidence(ConfidenceBasis.EXACT_QUOTE_ABSTRACT)
    paraphrase = score_confidence(ConfidenceBasis.PARAPHRASE)
    assert full > abstract > paraphrase


def test_confidence_rule_modifiers_and_bounds():
    page_bonus = score_confidence(ConfidenceBasis.EXACT_QUOTE_FULLTEXT, page_known=True)
    assert page_bonus == pytest.approx(0.95)  # clamped at upper bound
    broken = score_confidence(ConfidenceBasis.PARAPHRASE, chain_complete=False)
    assert broken == 0.2  # 0.4 base - 0.2 missing-link penalty
    assert confidence_label(page_bonus) == "high"
    assert confidence_label(score_confidence(ConfidenceBasis.EXACT_QUOTE_ABSTRACT)) == "medium"
    assert confidence_label(broken) == "low"


# ---------------------------------------------------------------------------
# 3. Deterministic quote location with page + offsets
# ---------------------------------------------------------------------------


def test_locate_quote_finds_page_and_offsets():
    start, end, page = locate_quote(FULLTEXT, "reaches a TOP-1 accuracy of 94.2 percent")
    assert page == 2
    assert FULLTEXT[start:end] == "reaches a top-1 accuracy of 94.2 percent"


def test_locate_quote_no_page_markers_returns_none_page():
    doc = "Plain abstract-only text with no page markers at all."
    start, end, page = locate_quote(doc, "no page markers")
    assert page is None
    assert doc[start:end] == "no page markers"


def test_locate_quote_miss_returns_none():
    assert locate_quote(FULLTEXT, "this sentence does not exist anywhere") is None


# ---------------------------------------------------------------------------
# 4. Pipeline integration: extraction output forced through anchored records
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_extractor_anchors_quotes_with_span_page_confidence(monkeypatch):
    from backend.app.agents.research_mode import extraction

    monkeypatch.setattr(
        extraction,
        "get_llm_for",
        lambda *args, **kwargs: _fake_llm(),
    )
    monkeypatch.setattr(
        extraction,
        "_safe_invoke_llm",
        _canned_llm_response([
            {
                "claim_summary": "The model attains state-of-the-art ImageNet accuracy.",
                "exact_quote": "The proposed model reaches a top-1 accuracy of 94.2 percent on ImageNet.",
                "source_section": "Results",
            }
        ]),
    )
    result = await extraction.evidence_extractor_agent({"paper_records": [dict(PAPER)]})
    records = result["evidence_records"]
    spans = result["evidence_spans"]
    assert len(records) == 1 and len(spans) == 1

    rec, span = records[0], spans[0]
    # Full traceability fields populated on the record itself.
    assert rec["paper_id"] == PAPER["paper_id"]
    assert rec["evidence_span_id"] == span["span_id"]
    assert rec["section"] == "Results"
    assert rec["page"] == 2
    assert rec["source_url"] == PAPER["source_url"]
    assert rec["doi"] == PAPER["doi"]
    assert rec["confidence"] >= 0.85
    assert rec["confidence_basis"] == ConfidenceBasis.EXACT_QUOTE_FULLTEXT
    assert rec["verification_status"] == "resolved"
    # Span carries the exact quoted text plus machine location.
    assert span["page"] == 2
    assert FULLTEXT[span["char_offset_start"]:span["char_offset_end"]] == span["text"]


@pytest.mark.asyncio
async def test_extractor_downgrades_unverifiable_paraphrase(monkeypatch):
    from backend.app.agents.research_mode import extraction

    monkeypatch.setattr(
        extraction,
        "get_llm_for",
        lambda *args, **kwargs: _fake_llm(),
    )
    monkeypatch.setattr(
        extraction,
        "_safe_invoke_llm",
        _canned_llm_response([
            {
                "claim_summary": "Invented claim about results never present in source.",
                "exact_quote": "Totally fabricated quotation that appears nowhere in the paper text.",
                "source_section": "Results",
            }
        ]),
    )
    result = await extraction.evidence_extractor_agent({"paper_records": [dict(PAPER)]})
    rec = result["evidence_records"][0]
    assert rec["confidence_basis"] == ConfidenceBasis.PARAPHRASE
    assert rec["verification_status"] == "unresolved"
    assert rec["confidence"] <= 0.5
    # Even downgraded claims keep the mandatory provenance fields populated.
    assert rec["paper_id"] and rec["section"] and rec["source_url"] is not None


@pytest.mark.asyncio
async def test_abstract_only_source_marks_location_unknown_not_guessed(monkeypatch):
    from backend.app.agents.research_mode import extraction

    paper = dict(PAPER)
    paper.pop("fulltext_excerpt")
    quote = "robustness of deep networks against distribution shift"

    monkeypatch.setattr(
        extraction,
        "get_llm_for",
        lambda *args, **kwargs: _fake_llm(),
    )
    monkeypatch.setattr(
        extraction,
        "_safe_invoke_llm",
        _canned_llm_response([
            {"claim_summary": "Survey covers distribution shift.", "exact_quote": quote,
             "source_section": "unknown"},
        ]),
    )
    result = await extraction.evidence_extractor_agent({"paper_records": [paper]})
    rec = result["evidence_records"][0]
    assert rec["section"] == "Abstract"
    assert rec["page"] is None  # abstract-only API result: page explicitly null
    assert rec["confidence_basis"] == ConfidenceBasis.EXACT_QUOTE_ABSTRACT


# ---------------------------------------------------------------------------
# 5. Traceability chain resolution + downgrade on broken links
# ---------------------------------------------------------------------------


def test_complete_chain_resolves():
    record = {
        "evidence_id": f"{PAPER['paper_id']}_ev001",
        "paper_id": PAPER["paper_id"],
        "claim_summary": "Model hits 94.2% top-1 on ImageNet.",
        "evidence_span_id": f"{PAPER['paper_id']}_sp001",
        "section": "Results",
        "page": 2,
        "confidence": 0.9,
        "confidence_basis": ConfidenceBasis.EXACT_QUOTE_FULLTEXT,
        "source_url": PAPER["source_url"],
        "doi": PAPER["doi"],
    }
    chains = build_evidence_chains([record], [PAPER])
    chain = chains[0]
    assert {"claim", "paper_id", "evidence", "section", "page", "confidence"}.issubset(chain)
    assert chain["verification_status"] == "resolved"
    assert chain["missing_links"] == []
    assert chain["evidence"]["exact_quote"] or chain["evidence"]["span_id"]

    claims = build_claims([record], [PAPER])
    c = claims[0]
    assert isinstance(c, Claim)
    assert c.paper_id == PAPER["paper_id"]
    assert c.evidence_span_id == record["evidence_span_id"]
    assert c.source_url == PAPER["source_url"]
    assert c.verification_status == "resolved"


def test_broken_chain_downgrades_to_unresolved_low_confidence():
    orphan_record = {
        "evidence_id": "ghost_ev001",
        "paper_id": "ghostpaper0000000",
        "claim_summary": "Claim pointing at a paper that is not in the corpus.",
        "section": "Results",
        "page": None,
        "confidence": 0.9,
        "confidence_basis": ConfidenceBasis.EXACT_QUOTE_FULLTEXT,
    }
    chains = build_evidence_chains([orphan_record], [PAPER])
    chain = chains[0]
    assert chain["verification_status"] == "unresolved"
    assert chain["confidence"] <= 0.4
    assert "paper_not_in_corpus" in chain["missing_links"]


def test_record_without_locator_gets_downgraded():
    """No source_url AND no doi anywhere in the chain -> unresolved downgrade."""
    paper_no_links = dict(PAPER)
    paper_no_links["doi"] = None
    paper_no_links["source_url"] = ""
    record = {
        "evidence_id": f"{PAPER['paper_id']}_ev001",
        "paper_id": PAPER["paper_id"],
        "claim_summary": "Any claim.",
        "section": "Results",
        "page": 2,
        "confidence": 0.9,
        "confidence_basis": ConfidenceBasis.EXACT_QUOTE_FULLTEXT,
    }
    chains = build_evidence_chains([record], [paper_no_links])
    assert chains[0]["verification_status"] == "unresolved"
    assert chains[0]["confidence"] < 0.9  # downgraded from exact-quote grade
    assert any("locator" in link for link in chains[0]["missing_links"])


@pytest.mark.asyncio
async def test_provenance_agent_emits_claims_and_keeps_state_consistent(monkeypatch):
    from backend.app.agents.research_mode import extraction

    good_record = {
        "evidence_id": f"{PAPER['paper_id']}_ev001",
        "paper_id": PAPER["paper_id"],
        "claim_summary": "Anchored claim with verbatim quote.",
        "exact_quote": "reaches a top-1 accuracy of 94.2 percent",
        "evidence_span_id": f"{PAPER['paper_id']}_sp001",
        "section": "Results",
        "page": 2,
        "confidence": 0.9,
        "confidence_basis": ConfidenceBasis.EXACT_QUOTE_FULLTEXT,
        "verification_status": "resolved",
        "source_url": PAPER["source_url"],
        "doi": PAPER["doi"],
    }
    ghost_record = {
        "evidence_id": "ghost_ev002",
        "paper_id": "notinthecorpus00",
        "claim_summary": "Orphaned claim.",
        "section": "unknown",
        "confidence": 0.5,
        "verification_status": "unresolved",
    }
    result = await extraction.provenance_agent({
        "evidence_records": [good_record, ghost_record],
        "paper_records": [PAPER],
    })
    # Orphan dropped, good record kept as a resolvable claim.
    kept_ids = [r["evidence_id"] for r in result["evidence_records"]]
    assert kept_ids == [good_record["evidence_id"]]
    claims = result["claims"]
    assert len(claims) == 1
    assert claims[0]["paper_id"] == PAPER["paper_id"]
    assert claims[0]["verification_status"] == "resolved"
    # Spans from the extractor remain attached for downstream consumers.
    assert isinstance(result.get("evidence_spans"), list)


@pytest.mark.asyncio
async def test_provenance_backfills_missing_fulltext_then_upgrades_anchor(monkeypatch):
    import backend.app.tools.fulltext_fetcher as ftf
    from backend.app.agents.research_mode import extraction

    paper = dict(PAPER)
    paper.pop("fulltext_excerpt")
    record = {
        "evidence_id": f"{paper['paper_id']}_ev001",
        "paper_id": paper["paper_id"],
        "claim_summary": "Paraphrased claim awaiting backfill.",
        "exact_quote": "The proposed model reaches a top-1 accuracy of 94.2 percent on ImageNet.",
        "section": "Results",
        "page": None,
        "confidence": 0.4,
        "confidence_basis": ConfidenceBasis.PARAPHRASE,
        "verification_status": "unresolved",
        "source_url": paper["source_url"],
        "doi": paper["doi"],
    }

    async def fake_fetch_fulltexts(papers):
        enriched = []
        for p in papers:
            pc = dict(p)
            pc["fulltext_excerpt"] = FULLTEXT
            enriched.append(pc)
        return enriched

    monkeypatch.setattr(ftf, "fetch_fulltexts", fake_fetch_fulltexts)

    result = await extraction.provenance_agent({
        "evidence_records": [record],
        "paper_records": [paper],
    })
    rec = result["evidence_records"][0]
    assert rec["confidence_basis"] == ConfidenceBasis.EXACT_QUOTE_FULLTEXT
    assert rec["verification_status"] == "resolved"
    assert rec["page"] == 2
