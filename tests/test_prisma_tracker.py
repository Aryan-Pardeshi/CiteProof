"""Unit tests for Stage 2: PRISMA Tracker Determinism & Figure Generation."""

import os

from backend.app.models.evidence import PaperRecord, PRISMATracker
from backend.app.tools.figures import render_evidence_table, render_prisma_diagram


def test_render_prisma_diagram_from_tracker(tmp_path):
    """Verify PRISMA diagram rendering directly from PRISMATracker model."""
    output_path = str(tmp_path / "prisma_test.png")
    tracker = PRISMATracker(
        records_identified=150,
        records_by_source={"openalex": 60, "semantic_scholar": 50, "arxiv": 40},
        duplicates_removed=30,
        records_after_dedup=120,
        records_screened=120,
        excluded_title_abstract=90,
        full_text_requested=30,
        full_text_assessed=30,
        excluded_full_text=5,
        studies_included=25,
        exclusion_reasons={"Low relevance": 90}
    )
    assert tracker.validate_invariants() == []
    res = render_prisma_diagram(tracker, output_path)
    assert res == output_path
    assert os.path.exists(output_path)
    assert os.path.getsize(output_path) > 1000


def test_render_evidence_table_with_paper_records(tmp_path):
    """Verify evidence mapping table rendering with PaperRecord objects."""
    output_path = str(tmp_path / "evidence_test.png")
    records = [
        PaperRecord(
            paper_id="p1",
            title="Transformer Scaling Laws for Large Language Models",
            relevance_score=9.5,
            citation_count=500,
            hypothesis_support={"H1": "Supports", "H2": "Partial"}
        ),
        PaperRecord(
            paper_id="p2",
            title="Emergent Abilities in Multimodal Artificial Intelligence",
            relevance_score=8.7,
            citation_count=250,
            hypothesis_support={"H1": "Refutes", "H2": "Supports"}
        ),
    ]
    hypotheses = ["H1: Larger parameter count improves zero-shot reasoning", "H2: Multimodal training reduces hallucination"]
    res = render_evidence_table(records, hypotheses, output_path)
    assert res == output_path
    assert os.path.exists(output_path)
    assert os.path.getsize(output_path) > 1000
