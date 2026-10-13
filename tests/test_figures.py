import os

import pytest

from backend.app.tools.figures import render_evidence_table, render_prisma_diagram


def test_render_prisma_diagram(tmp_path):
    output_path = str(tmp_path / "prisma.png")
    stats = {"retrieved": 120, "after_dedup": 95, "screened": 40, "included": 15}
    res = render_prisma_diagram(stats, output_path)
    assert res == output_path
    assert os.path.exists(output_path)
    assert os.path.getsize(output_path) > 0

def test_render_evidence_table_empty_if_no_support(tmp_path):
    output_path = str(tmp_path / "evidence.png")
    papers = [{"title": "Paper 1"}, {"title": "Paper 2"}]
    hypotheses = ["H1", "H2"]
    res = render_evidence_table(papers, hypotheses, output_path)
    assert res == ""
    assert not os.path.exists(output_path)

def test_render_evidence_table_with_support(tmp_path):
    output_path = str(tmp_path / "evidence.png")
    papers = [
        {"title": "Paper 1", "relevance_score": 9, "hypothesis_support": {"H1": "Supported", "H2": "Partial"}},
        {"title": "Paper 2", "relevance_score": 7, "hypothesis_support": {"H1": "Refuted", "H2": "Supported"}},
    ]
    hypotheses = ["H1", "H2"]
    res = render_evidence_table(papers, hypotheses, output_path)
    assert res == output_path
    assert os.path.exists(output_path)
    assert os.path.getsize(output_path) > 0
