from backend.app.agents.research_mode.validation import (
    validate_citations_in_text,
    extract_numerical_claims,
    CITATION_PATTERN,
)
from backend.app.models.evidence import PaperRecord, ValidationReport, PRISMATracker


def test_citation_pattern_regex():
    """Verify APA citation regex matches standard academic formats."""
    text = "Recent advances in transformers (Vaswani et al., 2017) and protein folding (Jumper & Hassabis, 2021) demonstrate scaling."
    matches = CITATION_PATTERN.findall(text)
    assert len(matches) == 2
    assert matches[0] == ("Vaswani et al.", "2017")
    assert matches[1] == ("Jumper & Hassabis", "2021")


def test_validate_citations_in_text():
    """Verify citation matcher correctly validates known papers and flags fake citations."""
    papers = [
        {"authors": ["Vaswani, A.", "Shazeer, N."], "year": "2017", "title": "Attention Is All You Need"},
        {"authors": ["Devlin, J.", "Chang, M."], "year": "2018", "title": "BERT"},
        {"authors": ["Orphan, X."], "year": "2020", "title": "Unused Reference Paper"}
    ]
    prose = "As demonstrated by (Vaswani et al., 2017) and verified by (Devlin et al., 2018), scaling works. However, (FakeAuthor et al., 2026) claimed otherwise."

    total, verified, unverified, orphans = validate_citations_in_text(prose, papers)
    assert total == 3
    assert verified == 2
    assert len(unverified) == 1
    assert "(FakeAuthor et al., 2026)" in unverified[0]
    assert len(orphans) == 1
    assert "Unused Reference Paper" in orphans[0]


def test_validate_citations_rejects_ambiguous_same_author_same_year():
    papers = [
        {"authors": ["Smith, A.", "Jones, B."], "year": "2024", "title": "Alpha Study"},
        {"authors": ["Smith, A.", "Brown, C."], "year": "2024", "title": "Beta Study"},
    ]

    total, verified, unverified, _ = validate_citations_in_text(
        "Prior work (Smith et al., 2024) reported the effect.", papers
    )

    assert total == 1
    assert verified == 0
    assert unverified == ["(Smith et al., 2024)"]


def test_validate_citations_uses_second_author_to_disambiguate():
    papers = [
        {"authors": ["Smith, A.", "Jones, B."], "year": "2024", "title": "Alpha Study"},
        {"authors": ["Smith, A.", "Brown, C."], "year": "2024", "title": "Beta Study"},
    ]

    total, verified, unverified, _ = validate_citations_in_text(
        "Prior work (Smith & Brown, 2024) reported the effect.", papers
    )

    assert (total, verified, unverified) == (1, 1, [])


def test_validate_citations_supports_year_suffixes():
    papers = [
        {"authors": ["Smith, A.", "Jones, B."], "year": "2024", "title": "Alpha Study"},
        {"authors": ["Smith, A.", "Jones, B."], "year": "2024", "title": "Beta Study"},
    ]

    total, verified, unverified, _ = validate_citations_in_text(
        "The later paper (Smith et al., 2024b) extends the result.", papers
    )

    assert (total, verified, unverified) == (1, 1, [])


def test_validate_citations_normalizes_diacritics_and_particles():
    papers = [
        {"authors": ["Müller, A."], "year": "2021", "title": "Umlaut Study"},
        {"authors": ["de la Cruz, M."], "year": "2022", "title": "Particle Study"},
    ]
    prose = "Results agree with (Mueller, 2021) and (de la Cruz et al., 2022)."

    total, verified, unverified, _ = validate_citations_in_text(prose, papers)

    assert (total, verified, unverified) == (2, 2, [])


def test_numerical_claim_regex():
    """Verify quantitative sentence extractor matches percentage and benchmark sentences."""
    text = "Our model achieves 94.2% accuracy on ImageNet. Previous approaches scored 88.5 BLEU. Qualitative behavior is sound."
    sentences = extract_numerical_claims(text)
    assert len(sentences) == 2
    assert "94.2% accuracy" in sentences[0]
    assert "88.5 BLEU" in sentences[1]
