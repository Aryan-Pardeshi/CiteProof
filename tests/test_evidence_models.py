"""Unit tests for Stage 1: Structured Evidence Models & PRISMA Invariants."""

import pytest
from pydantic import ValidationError

from backend.app.models.evidence import (
    Claim,
    EvidenceRecord,
    PaperRecord,
    PRISMATracker,
    ReviewClaim,
    make_claim_id,
    make_evidence_id,
    make_paper_id,
)


def test_make_paper_id_deterministic():
    """Verify that paper ID generation is deterministic and handles DOI vs title."""
    id1 = make_paper_id(doi="10.1038/s41586-021-03819-2", title="Highly accurate protein structure prediction with AlphaFold", year="2021")
    id2 = make_paper_id(doi="https://doi.org/10.1038/s41586-021-03819-2", title="Different Title", year="2021")
    assert id1 == id2
    assert len(id1) == 16

    # Fallback to normalized title
    id_no_doi_1 = make_paper_id(doi=None, title="Quantum Computing in 2026: A Comprehensive Survey.", year="2026")
    id_no_doi_2 = make_paper_id(doi="", title="quantum computing in 2026 a comprehensive survey", year="2026")
    assert id_no_doi_1 == id_no_doi_2
    assert len(id_no_doi_1) == 16


def test_make_evidence_and_claim_id():
    """Verify scoped IDs for evidence records and claims."""
    ev_id = make_evidence_id("a3f2b8c1d4e9f012", 1)
    assert ev_id == "a3f2b8c1d4e9f012_ev001"

    claim_id = make_claim_id("literature_review", 7)
    assert claim_id == "literatu_cl007"


def test_paper_record_schema():
    """Test PaperRecord validation, defaults, and serialization."""
    paper = PaperRecord(
        paper_id="test_id_12345678",
        title="Attention Is All You Need",
        authors=["Vaswani, A.", "Shazeer, N."],
        year="2017",
        retrieval_source="arxiv",
        citation_count=100000,
        screening_status="included",
    )
    assert paper.quality_rating == "Unclear"
    assert paper.study_type == "empirical"
    d = paper.model_dump()
    assert d["paper_id"] == "test_id_12345678"
    assert d["citation_count"] == 100000

    # Auto-generate ID in from_dict
    paper2 = PaperRecord.from_dict({
        "title": "BERT: Pre-training of Deep Bidirectional Transformers",
        "authors": ["Devlin, J."],
        "year": "2018",
        "doi": "10.18653/v1/N19-1423",
        "source_url": "https://aclanthology.org/N19-1423",
    })
    assert len(paper2.paper_id) == 16


def test_evidence_record_schema():
    """Test EvidenceRecord validation and hypothesis support mapping."""
    ev = EvidenceRecord(
        evidence_id="paper1_ev001",
        paper_id="paper1",
        claim_summary="Transformer models achieve 28.4 BLEU on WMT 2014 English-to-German translation task.",
        exact_quote="On the WMT 2014 English-to-German translation task, the big transformer model establishes a new state-of-the-art BLEU score of 28.4.",
        source_section="Section 5.1",
        metric_name="BLEU",
        baseline_value=26.3,
        reported_value=28.4,
        effect_direction="positive",
        reproducibility_available=True,
        hypothesis_relevance={"H1": "Supports"}
    )
    assert ev.effect_direction == "positive"
    assert ev.reported_value == 28.4
    assert ev.hypothesis_relevance["H1"] == "Supports"


def test_review_claim_status_vocabulary_has_no_verified():
    """'verified' would imply a passage-supports-sentence check that no validator performs.

    A claim whose claim -> evidence -> paper -> locator chain resolves is 'resolved'.
    """
    kwargs = {"claim_id": "results_cl001", "claim_text": "A claim.", "target_section": "results"}
    assert ReviewClaim(**kwargs, validation_status="resolved").validation_status == "resolved"
    with pytest.raises(ValidationError):
        ReviewClaim(**kwargs, validation_status="verified")


def test_evidence_record_verification_status_means_resolution_only():
    """'resolved' = quote located and chain complete; it makes no claim about support."""
    base = {"evidence_id": "p_ev001", "paper_id": "p", "claim_summary": "Any finding."}
    assert EvidenceRecord(**base).verification_status == "unresolved"
    assert EvidenceRecord(**base, verification_status="resolved").verification_status == "resolved"
    for legacy in ("verified", "unverified"):
        with pytest.raises(ValidationError):
            EvidenceRecord(**base, verification_status=legacy)


def test_claim_verification_status_means_resolution_only():
    base = {"text": "A claim.", "paper_id": "p", "evidence_span_id": "p_sp001"}
    assert Claim(**base).verification_status == "unresolved"
    assert Claim(**base, verification_status="resolved").verification_status == "resolved"
    with pytest.raises(ValidationError):
        Claim(**base, verification_status="verified")


def test_prisma_tracker_invariants():
    """Test mathematical conservation laws of the PRISMATracker."""
    tracker = PRISMATracker(
        records_identified=100,
        duplicates_removed=20,
        records_after_dedup=80,
        records_screened=80,
        excluded_title_abstract=50,
        full_text_requested=30,
        full_text_assessed=30,
        excluded_full_text=10,
        studies_included=20
    )
    errors = tracker.validate_invariants()
    assert len(errors) == 0, f"Unexpected errors: {errors}"

    # Break identified - duplicates != after_dedup
    broken_tracker = PRISMATracker(
        records_identified=100,
        duplicates_removed=20,
        records_after_dedup=75,  # Should be 80!
        records_screened=75,
        excluded_title_abstract=50,
        full_text_requested=25,
        full_text_assessed=25,
        excluded_full_text=5,
        studies_included=20
    )
    broken_errors = broken_tracker.validate_invariants()
    assert len(broken_errors) > 0
    assert "identified (100) - duplicates (20) != after_dedup (75)" in broken_errors[0]
