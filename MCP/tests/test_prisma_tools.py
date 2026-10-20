# SPDX-License-Identifier: MIT
"""Deterministic offline tests for PRISMA tracking, screening logs, and agreement tools."""

from __future__ import annotations

import pytest
from citeproof_mcp import store

from tests.conftest import call


@pytest.mark.asyncio
async def test_search_and_dedup_success_and_validation() -> None:
    """Test recording searches, deduplication, and input validation errors."""
    rev_res = await call("review_create", title="Diabetes Screening", question="Efficacy?")
    assert rev_res["ok"] is True
    rev_id = rev_res["review_id"]

    # 1. Successful search recording
    s1 = await call(
        "prisma_record_search",
        review_id=rev_id,
        source="PubMed",
        query="diabetes AND metformin",
        date_run="2026-01-15",
        hits=120,
    )
    assert s1["ok"] is True
    assert s1["records_identified"] == 120
    assert s1["records_by_source"] == {"PubMed": 120}

    s2 = await call(
        "prisma_record_search",
        review_id=rev_id,
        source="Embase",
        query="diabetes AND metformin",
        date_run="2026-01-16",
        hits=80,
    )
    assert s2["ok"] is True
    assert s2["records_identified"] == 200
    assert s2["records_by_source"] == {"PubMed": 120, "Embase": 80}

    # 2. Validation failures for search
    bad_hits = await call(
        "prisma_record_search",
        review_id=rev_id,
        source="Cochrane",
        query="q",
        date_run="2026-01-17",
        hits=-5,
    )
    assert bad_hits["ok"] is False
    assert "hits" in bad_hits["error"]

    bad_date = await call(
        "prisma_record_search",
        review_id=rev_id,
        source="Cochrane",
        query="q",
        date_run="not-a-date",
        hits=10,
    )
    assert bad_date["ok"] is False
    assert "date_run" in bad_date["error"]

    empty_source = await call(
        "prisma_record_search",
        review_id=rev_id,
        source="   ",
        query="q",
        date_run="2026-01-17",
        hits=10,
    )
    assert empty_source["ok"] is False

    empty_query = await call(
        "prisma_record_search",
        review_id=rev_id,
        source="Cochrane",
        query="",
        date_run="2026-01-17",
        hits=10,
    )
    assert empty_query["ok"] is False

    # 3. Successful deduplication recording
    d1 = await call(
        "prisma_record_dedup",
        review_id=rev_id,
        duplicates_removed=45,
        method="DOI and Title normalization",
    )
    assert d1["ok"] is True
    assert d1["duplicates_removed"] == 45
    assert d1["records_after_dedup"] == 155

    # 4. Dedup validation errors
    # Exceeding total hits (250 > 200)
    over_dedup = await call(
        "prisma_record_dedup",
        review_id=rev_id,
        duplicates_removed=250,
        method="DOI match",
    )
    assert over_dedup["ok"] is False
    assert "cannot exceed" in over_dedup["error"]

    # Negative duplicates
    neg_dedup = await call(
        "prisma_record_dedup",
        review_id=rev_id,
        duplicates_removed=-1,
        method="DOI match",
    )
    assert neg_dedup["ok"] is False
    assert "duplicates_removed" in neg_dedup["error"]


@pytest.mark.asyncio
async def test_screening_upsert_validation_and_filters() -> None:
    """Test single and batch screening records, primary key upsert, validation, and filters."""
    rev_res = await call("review_create", title="Oncology Review", question="Immunotherapy")
    rev_id = rev_res["review_id"]

    # 1. Initial include decision
    rec1 = await call(
        "screening_record",
        review_id=rev_id,
        paper_ref="10.1056/nejmoa12345",
        stage="title_abstract",
        decision="include",
        reviewer="primary",
        title="Immunotherapy Trial",
    )
    assert rec1["ok"] is True
    assert rec1["updated"] is False

    # 2. Upsert of same paper and stage: updated flag is True
    rec1_up = await call(
        "screening_record",
        review_id=rev_id,
        paper_ref="10.1056/nejmoa12345",
        stage="title_abstract",
        decision="exclude",
        reason="Wrong patient population",
        reviewer="primary",
        title="Immunotherapy Trial (Updated)",
    )
    assert rec1_up["ok"] is True
    assert rec1_up["updated"] is True

    # 3. Exclude requires non-empty reason
    rec_bad_reason = await call(
        "screening_record",
        review_id=rev_id,
        paper_ref="pmid:12345678",
        stage="title_abstract",
        decision="exclude",
        reason="",
        reviewer="primary",
    )
    assert rec_bad_reason["ok"] is False
    assert "reason" in rec_bad_reason["error"]

    # 4. Bad stage and bad decision
    bad_stage = await call(
        "screening_record",
        review_id=rev_id,
        paper_ref="pmid:12345678",
        stage="full_pdf",
        decision="include",
        reviewer="primary",
    )
    assert bad_stage["ok"] is False
    assert "stage" in bad_stage["error"]

    bad_dec = await call(
        "screening_record",
        review_id=rev_id,
        paper_ref="pmid:12345678",
        stage="title_abstract",
        decision="maybe",
        reviewer="primary",
    )
    assert bad_dec["ok"] is False
    assert "decision" in bad_dec["error"]

    # 5. Bad paper_ref format (must start with 10., pmid:, or title:)
    bad_ref = await call(
        "screening_record",
        review_id=rev_id,
        paper_ref="arxiv:2104.12345",
        stage="title_abstract",
        decision="include",
        reviewer="primary",
    )
    assert bad_ref["ok"] is False
    assert "paper_ref" in bad_ref["error"]

    # 6. Full-text decision without prior title_abstract include produces a warning
    ft_warn = await call(
        "screening_record",
        review_id=rev_id,
        paper_ref="title:novel therapy|2024",
        stage="full_text",
        decision="include",
        reviewer="primary",
    )
    assert ft_warn["ok"] is True
    assert "warnings" in ft_warn
    assert len(ft_warn["warnings"]) == 1

    # 7. Batch screening: screening_record_many
    # All-or-nothing test: third item is invalid (exclude without reason)
    batch_with_err = [
        {
            "paper_ref": "10.1001/jama.2024.1",
            "stage": "title_abstract",
            "decision": "include",
            "reviewer": "primary",
        },
        {
            "paper_ref": "10.1001/jama.2024.2",
            "stage": "title_abstract",
            "decision": "include",
            "reviewer": "primary",
        },
        {
            "paper_ref": "10.1001/jama.2024.3",
            "stage": "title_abstract",
            "decision": "exclude",
            "reason": "",  # Missing reason
            "reviewer": "primary",
        },
    ]
    batch_fail = await call("screening_record_many", review_id=rev_id, decisions=batch_with_err)
    assert batch_fail["ok"] is False
    assert batch_fail["index"] == 2

    # Verify nothing was written from the failed batch
    cur_list = await call("screening_list", review_id=rev_id, stage="title_abstract")
    existing_refs = {r["paper_ref"] for r in cur_list["records"]}
    assert "10.1001/jama.2024.1" not in existing_refs
    assert "10.1001/jama.2024.2" not in existing_refs

    # Valid batch write
    valid_batch = [
        {
            "paper_ref": "10.1001/jama.2024.1",
            "stage": "title_abstract",
            "decision": "include",
            "reviewer": "primary",
        },
        {
            "paper_ref": "10.1001/jama.2024.2",
            "stage": "title_abstract",
            "decision": "exclude",
            "reason": "Not randomized",
            "reviewer": "primary",
        },
    ]
    batch_ok = await call("screening_record_many", review_id=rev_id, decisions=valid_batch)
    assert batch_ok["ok"] is True
    assert batch_ok["count"] == 2
    assert batch_ok["inserted_count"] == 2

    # 8. screening_list filters and cap
    full_list = await call("screening_list", review_id=rev_id)
    assert full_list["ok"] is True
    assert full_list["truncated"] is False
    assert len(full_list["records"]) >= 4

    filtered_ta = await call("screening_list", review_id=rev_id, stage="title_abstract")
    for r in filtered_ta["records"]:
        assert r["stage"] == "title_abstract"

    capped_list = await call("screening_list", review_id=rev_id, limit=2)
    assert len(capped_list["records"]) == 2
    assert capped_list["truncated"] is True


@pytest.mark.asyncio
async def test_screening_agreement_cohen_kappa() -> None:
    """Test Cohen's kappa calculation, marginals, perfect agreement, null case, and single-rater counts.

    Hand-computed arithmetic for the 10-paper scenario:
    Shared papers N = 10, stage = 'title_abstract'
    Decisions:
      Both include (a11): 4
      A include, B exclude (a12): 1
      A exclude, B include (a21): 2
      Both exclude (a22): 3
    Total agreements = a11 + a22 = 4 + 3 = 7
    Observed agreement po = 7 / 10 = 0.7000

    Marginal probabilities:
      Reviewer A:
        A_include = 4 + 1 = 5  -> P(A_inc) = 5/10 = 0.50
        A_exclude = 2 + 3 = 5  -> P(A_exc) = 5/10 = 0.50
      Reviewer B:
        B_include = 4 + 2 = 6  -> P(B_inc) = 6/10 = 0.60
        B_exclude = 1 + 3 = 4  -> P(B_exc) = 4/10 = 0.40

    Expected agreement pe:
      pe = P(A_inc)*P(B_inc) + P(A_exc)*P(B_exc)
         = (0.50 * 0.60) + (0.50 * 0.40)
         = 0.30 + 0.20 = 0.5000

    Kappa:
      kappa = (po - pe) / (1 - pe)
            = (0.70 - 0.50) / (1.0 - 0.50)
            = 0.20 / 0.50 = 0.4000
    Landis and Koch band for 0.40: 'fair' (0.20 < kappa <= 0.40).
    """
    rev_res = await call("review_create", title="Kappa Test", question="Agreement")
    rev_id = rev_res["review_id"]

    # 1. Populate the 10 shared papers according to the hand-computed table
    # Papers 1-4: both include
    # Paper 5: A include, B exclude
    # Papers 6-7: A exclude, B include
    # Papers 8-10: both exclude
    shared_data = [
        ("10.1001/p1", "include", None, "include", None),
        ("10.1001/p2", "include", None, "include", None),
        ("10.1001/p3", "include", None, "include", None),
        ("10.1001/p4", "include", None, "include", None),
        ("10.1001/p5", "include", None, "exclude", "Not relevant"),
        ("10.1001/p6", "exclude", "Animal study", "include", None),
        ("10.1001/p7", "exclude", "In vitro", "include", None),
        ("10.1001/p8", "exclude", "Editorial", "exclude", "Wrong study design"),
        ("10.1001/p9", "exclude", "Duplicate", "exclude", "Duplicate record"),
        ("10.1001/p10", "exclude", "No outcome", "exclude", "Missing data"),
    ]

    for pref, dec_a, re_a, dec_b, re_b in shared_data:
        await call(
            "screening_record",
            review_id=rev_id,
            paper_ref=pref,
            stage="title_abstract",
            decision=dec_a,
            reason=re_a,
            reviewer="alice",
            title=f"Title for {pref}",
        )
        await call(
            "screening_record",
            review_id=rev_id,
            paper_ref=pref,
            stage="title_abstract",
            decision=dec_b,
            reason=re_b,
            reviewer="bob",
            title=f"Title for {pref}",
        )

    # Add 2 papers seen ONLY by alice, and 3 papers seen ONLY by bob
    await call(
        "screening_record",
        review_id=rev_id,
        paper_ref="10.1001/only_a1",
        stage="title_abstract",
        decision="include",
        reviewer="alice",
    )
    await call(
        "screening_record",
        review_id=rev_id,
        paper_ref="10.1001/only_a2",
        stage="title_abstract",
        decision="exclude",
        reason="Off topic",
        reviewer="alice",
    )

    await call(
        "screening_record",
        review_id=rev_id,
        paper_ref="10.1001/only_b1",
        stage="title_abstract",
        decision="include",
        reviewer="bob",
    )
    await call(
        "screening_record",
        review_id=rev_id,
        paper_ref="10.1001/only_b2",
        stage="title_abstract",
        decision="include",
        reviewer="bob",
    )
    await call(
        "screening_record",
        review_id=rev_id,
        paper_ref="10.1001/only_b3",
        stage="title_abstract",
        decision="exclude",
        reason="Foreign language",
        reviewer="bob",
    )

    agr = await call(
        "screening_agreement",
        review_id=rev_id,
        stage="title_abstract",
        reviewer_a="alice",
        reviewer_b="bob",
    )
    assert agr["ok"] is True
    assert agr["shared"] == 10
    assert agr["agree"] == 7
    assert agr["po"] == 0.7
    assert agr["pe"] == 0.5
    assert agr["kappa"] == 0.4
    assert agr["interpretation"] == "fair"
    assert len(agr["disagreements"]) == 3
    assert agr["only_a"] == 2
    assert agr["only_b"] == 3

    # 2. Perfect agreement scenario (kappa = 1.0)
    rev_perf = await call("review_create", title="Perfect Agreement", question="Kappa 1.0")
    perf_id = rev_perf["review_id"]
    for i in range(1, 4):
        pref = f"10.2000/perf{i}"
        await call(
            "screening_record",
            review_id=perf_id,
            paper_ref=pref,
            stage="full_text",
            decision="include",
            reviewer="rater1",
        )
        await call(
            "screening_record",
            review_id=perf_id,
            paper_ref=pref,
            stage="full_text",
            decision="include",
            reviewer="rater2",
        )
    for i in range(4, 6):
        pref = f"10.2000/perf{i}"
        await call(
            "screening_record",
            review_id=perf_id,
            paper_ref=pref,
            stage="full_text",
            decision="exclude",
            reason="Excluded",
            reviewer="rater1",
        )
        await call(
            "screening_record",
            review_id=perf_id,
            paper_ref=pref,
            stage="full_text",
            decision="exclude",
            reason="Excluded",
            reviewer="rater2",
        )

    perf_agr = await call(
        "screening_agreement",
        review_id=perf_id,
        stage="full_text",
        reviewer_a="rater1",
        reviewer_b="rater2",
    )
    assert perf_agr["ok"] is True
    assert perf_agr["shared"] == 5
    assert perf_agr["agree"] == 5
    assert perf_agr["po"] == 1.0
    assert perf_agr["kappa"] == 1.0
    assert perf_agr["interpretation"] == "almost perfect"
    assert perf_agr["disagreements"] == []

    # 3. All decisions in the same category -> pe == 1.0 -> kappa is null
    rev_null = await call("review_create", title="Null Kappa", question="All Include")
    null_id = rev_null["review_id"]
    for i in range(1, 5):
        pref = f"10.3000/same{i}"
        await call(
            "screening_record",
            review_id=null_id,
            paper_ref=pref,
            stage="title_abstract",
            decision="include",
            reviewer="u1",
        )
        await call(
            "screening_record",
            review_id=null_id,
            paper_ref=pref,
            stage="title_abstract",
            decision="include",
            reviewer="u2",
        )

    null_agr = await call(
        "screening_agreement",
        review_id=null_id,
        stage="title_abstract",
        reviewer_a="u1",
        reviewer_b="u2",
    )
    assert null_agr["ok"] is True
    assert null_agr["shared"] == 4
    assert null_agr["pe"] == 1.0
    assert null_agr["kappa"] is None
    assert null_agr["interpretation"] == "undefined"


@pytest.mark.asyncio
async def test_prisma_status_invariants_and_reviewers() -> None:
    """Test full worked PRISMA status workflow, invariant validation, and reviewer rules."""
    rev_res = await call("review_create", title="Full PRISMA Review", question="Systematic Flow")
    rev_id = rev_res["review_id"]

    # 1. With no decisions or searches, counts are zero and invariants hold
    init_status = await call("prisma_status", review_id=rev_id)
    assert init_status["ok"] is True
    assert init_status["records_identified"] == 0
    assert init_status["records_screened"] == 0
    assert init_status["invariants_valid"] is True
    assert init_status["reviewer_used"] is None

    # 2. Add searches and dedup
    # Identified: 100
    await call(
        "prisma_record_search",
        review_id=rev_id,
        source="PubMed",
        query="stroke",
        date_run="2026-02-01",
        hits=60,
    )
    await call(
        "prisma_record_search",
        review_id=rev_id,
        source="Scopus",
        query="stroke",
        date_run="2026-02-01",
        hits=40,
    )
    # Dedup: 20 -> records_after_dedup = 80
    await call(
        "prisma_record_dedup",
        review_id=rev_id,
        duplicates_removed=20,
        method="automated",
    )

    # 3. Add screening decisions by 'primary' reviewer
    # 80 title/abstract screened: 60 include, 20 exclude
    ta_decisions = []
    for i in range(1, 61):
        ta_decisions.append({
            "paper_ref": f"10.5000/study{i}",
            "stage": "title_abstract",
            "decision": "include",
            "reviewer": "primary",
        })
    for i in range(61, 81):
        ta_decisions.append({
            "paper_ref": f"10.5000/study{i}",
            "stage": "title_abstract",
            "decision": "exclude",
            "reason": "Not human study",
            "reviewer": "primary",
        })
    await call("screening_record_many", review_id=rev_id, decisions=ta_decisions)

    # 60 full_text assessed: 50 include, 10 exclude
    ft_decisions = []
    for i in range(1, 51):
        ft_decisions.append({
            "paper_ref": f"10.5000/study{i}",
            "stage": "full_text",
            "decision": "include",
            "reviewer": "primary",
        })
    for i in range(51, 61):
        ft_decisions.append({
            "paper_ref": f"10.5000/study{i}",
            "stage": "full_text",
            "decision": "exclude",
            "reason": "Insufficient follow-up",
            "reviewer": "primary",
        })
    await call("screening_record_many", review_id=rev_id, decisions=ft_decisions)

    # Check status with primary reviewer
    stat = await call("prisma_status", review_id=rev_id)
    assert stat["ok"] is True
    assert stat["reviewer_used"] == "primary"
    assert stat["records_identified"] == 100
    assert stat["duplicates_removed"] == 20
    assert stat["records_after_dedup"] == 80
    assert stat["records_screened"] == 80
    assert stat["excluded_title_abstract"] == 20
    assert stat["full_text_requested"] == 60
    assert stat["full_text_assessed"] == 60
    assert stat["full_text_unavailable"] == 0
    assert stat["excluded_full_text"] == 10
    assert stat["studies_included"] == 50
    # PRISMA reports reasons for full-text exclusions; title/abstract reasons are kept separately.
    assert stat["exclusion_reasons"] == {"insufficient follow-up": 10}
    assert stat["exclusion_reasons_title_abstract"] == {"not human study": 20}
    assert stat["full_text_pending"] == 0
    assert stat["complete"] is True
    assert stat["invariants_valid"] is True
    assert stat["invariant_errors"] == []
    assert stat["warnings"] == []

    # 4. Reviewer selection rules
    # Add a second reviewer 'reviewer_two'
    await call(
        "screening_record",
        review_id=rev_id,
        paper_ref="10.5000/study1",
        stage="title_abstract",
        decision="include",
        reviewer="reviewer_two",
    )
    # Because 'primary' exists among reviewers, calling without reviewer defaults to 'primary'
    stat_prim = await call("prisma_status", review_id=rev_id)
    assert stat_prim["reviewer_used"] == "primary"

    # Explicit reviewer requested
    stat_two = await call("prisma_status", review_id=rev_id, reviewer="reviewer_two")
    assert stat_two["reviewer_used"] == "reviewer_two"
    assert stat_two["records_screened"] == 1

    # In a review with multiple reviewers and no 'primary', prisma_status returns ok: false
    rev_amb = await call("review_create", title="Ambiguous Reviewers", question="Who is primary?")
    amb_id = rev_amb["review_id"]
    await call(
        "screening_record",
        review_id=amb_id,
        paper_ref="10.6000/p1",
        stage="title_abstract",
        decision="include",
        reviewer="alice",
    )
    await call(
        "screening_record",
        review_id=amb_id,
        paper_ref="10.6000/p1",
        stage="title_abstract",
        decision="include",
        reviewer="bob",
    )
    amb_stat = await call("prisma_status", review_id=amb_id)
    assert amb_stat["ok"] is False
    assert "Multiple reviewers" in amb_stat["error"]

    # 5. Invariant violation test
    # In a fresh review, manually corrupt screening data with raw SQLite to break an invariant
    rev_inv = await call("review_create", title="Broken Invariant Review", question="Invariant check")
    inv_id = rev_inv["review_id"]
    # Record searches: 50 hits
    await call(
        "prisma_record_search",
        review_id=inv_id,
        source="PubMed",
        query="cancer",
        date_run="2026-03-01",
        hits=50,
    )
    # Direct database insertion to force screened - excluded != requested:
    # 2 records screened, 0 excluded, but only 1 included (second record has decision 'other')
    conn = store.open_review(inv_id)
    try:
        conn.execute(
            """
            INSERT INTO screening (paper_ref, stage, reviewer, decision, reason, title, updated_at)
            VALUES ('10.7000/p1', 'title_abstract', 'primary', 'include', NULL, NULL, '2026-03-01T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO screening (paper_ref, stage, reviewer, decision, reason, title, updated_at)
            VALUES ('10.7000/p2', 'title_abstract', 'primary', 'other_decision', NULL, NULL, '2026-03-01T00:00:00Z')
            """
        )
        conn.commit()
    finally:
        conn.close()

    broken_stat = await call("prisma_status", review_id=inv_id)
    assert broken_stat["ok"] is True
    assert broken_stat["invariants_valid"] is False
    assert len(broken_stat["invariant_errors"]) > 0
    assert "PRISMA Invariant Broken" in broken_stat["invariant_errors"][0]


@pytest.mark.asyncio
async def test_prisma_flow_diagram_formats_and_escaping() -> None:
    """Test Mermaid, SVG, and Markdown flow diagrams, number presence, escaping, and format errors."""
    rev_res = await call("review_create", title="Flow Diagram Review", question="Visual flow")
    rev_id = rev_res["review_id"]

    # Add searches and dedup
    await call(
        "prisma_record_search",
        review_id=rev_id,
        source="PubMed",
        query="ai",
        date_run="2026-03-10",
        hits=100,
    )
    await call(
        "prisma_record_dedup",
        review_id=rev_id,
        duplicates_removed=15,
        method="Crossref DOI matching",
    )

    # Full-text exclusion reason with injection attempt / special characters (the diagram
    # shows full-text exclusion reasons, as PRISMA 2020 does)
    malicious_reason = '<script>alert("xss")</script> & "quotes" and \'apostrophes\''
    for ref in ("10.8000/p1", "10.8000/p2"):
        await call(
            "screening_record",
            review_id=rev_id,
            paper_ref=ref,
            stage="title_abstract",
            decision="include",
            reviewer="primary",
        )
    await call(
        "screening_record",
        review_id=rev_id,
        paper_ref="10.8000/p2",
        stage="full_text",
        decision="exclude",
        reason=malicious_reason,
        reviewer="primary",
    )
    await call(
        "screening_record",
        review_id=rev_id,
        paper_ref="10.8000/p1",
        stage="full_text",
        decision="include",
        reviewer="primary",
    )

    # 1. Mermaid format test
    m_res = await call("prisma_flow_diagram", review_id=rev_id, format="mermaid")
    assert m_res["ok"] is True
    mermaid_text = m_res["diagram"]
    assert "flowchart TD" in mermaid_text
    assert "Records identified from databases (n = 100)" in mermaid_text
    assert "Duplicates removed (n = 15)" in mermaid_text
    assert "Records after deduplication (n = 85)" in mermaid_text
    # Agent text uses Mermaid's own entity codes, so no raw tag or quote breaks the label
    assert "<script>" not in mermaid_text
    assert "#lt;script#gt;" in mermaid_text
    assert '"xss"' not in mermaid_text
    # Only 2 of 85 records screened: the diagram says the counts are incomplete
    assert m_res["complete"] is False

    # 2. SVG format test
    s_res = await call("prisma_flow_diagram", review_id=rev_id, format="svg")
    assert s_res["ok"] is True
    svg_text = s_res["diagram"]
    assert "<svg" in svg_text
    assert 'viewBox="0 0 800 950"' in svg_text
    assert "Records identified from databases: 100" in svg_text
    assert "Duplicates removed: 15" in svg_text
    # XML escaped dynamic string: no raw script tags or quotes
    assert "<script>" not in svg_text
    assert "&lt;script&gt;" in svg_text
    assert "&quot;xss&quot;" in svg_text

    # 3. Markdown format test
    md_res = await call("prisma_flow_diagram", review_id=rev_id, format="markdown")
    assert md_res["ok"] is True
    md_text = md_res["diagram"]
    assert "# PRISMA 2020 Flow Summary" in md_text
    assert "1. **Identification**" in md_text
    assert "Records identified from searches: 100" in md_text
    assert "Duplicate records removed: 15" in md_text
    assert "Studies included in review: 1" in md_text
    # Never produce HTML in Markdown
    assert "<script>" not in md_text
    assert "&lt;script&gt;" in md_text

    # 4. Unknown format rejection
    bad_fmt = await call("prisma_flow_diagram", review_id=rev_id, format="pdf")
    assert bad_fmt["ok"] is False
    assert "format" in bad_fmt["error"]


@pytest.mark.asyncio
async def test_prisma_checklist_27_items_and_oversize() -> None:
    """Test PRISMA 2020 27-item checklist keyword matcher and size limits."""
    rev_res = await call("review_create", title="Checklist Review", question="PRISMA 2020 audit")
    rev_id = rev_res["review_id"]

    # First line is the title; the text mentions only "search strategy" and "limitations"
    report_text = (
        "Diet and blood pressure: a systematic review\n"
        "We executed a comprehensive search strategy. "
        "The primary limitations relate to observational studies."
    )

    chk = await call("prisma_checklist", review_id=rev_id, report_text=report_text)
    assert chk["ok"] is True
    assert chk["total"] == 27
    assert chk["found_count"] == 3
    assert len(chk["items"]) == 27
    assert len(chk["missing"]) == 24

    found = {it["item"]: it for it in chk["items"] if it["found"]}
    assert set(found) == {1, 7, 23}
    assert found[7]["topic"] == "Search strategy"
    assert found[23]["matched"].lower() == "limitations"
    assert "Search strategy" not in chk["missing"]

    # Items follow PRISMA 2020 numbering: 24 registration, 25 support, 26 competing interests, 27 data availability
    topics = {it["item"]: it["topic"] for it in chk["items"]}
    assert topics[24] == "Registration and protocol"
    assert topics[25] == "Support"
    assert topics[26] == "Competing interests"
    assert topics[27].startswith("Availability of data")

    assert "Keyword check of the report text." in chk["note"]

    # Oversize report text (> 400,000 characters) is rejected
    oversize_text = "word " * 100000  # 500,000 characters
    chk_over = await call("prisma_checklist", review_id=rev_id, report_text=oversize_text)
    assert chk_over["ok"] is False
    assert "limit is 400000" in chk_over["error"]


@pytest.mark.asyncio
async def test_unknown_review_id_returns_error_for_all_tools() -> None:
    """Verify that every tool fails gracefully with ok: false when given a non-existent review_id."""
    bad_rev = "rev-nonexistent01"

    t1 = await call(
        "prisma_record_search",
        review_id=bad_rev,
        source="PubMed",
        query="query",
        date_run="2026-01-01",
        hits=10,
    )
    assert t1["ok"] is False
    assert "unknown review_id" in t1["error"]

    t2 = await call(
        "prisma_record_dedup",
        review_id=bad_rev,
        duplicates_removed=5,
        method="manual",
    )
    assert t2["ok"] is False
    assert "unknown review_id" in t2["error"]

    t3 = await call(
        "screening_record",
        review_id=bad_rev,
        paper_ref="10.1000/1",
        stage="title_abstract",
        decision="include",
    )
    assert t3["ok"] is False
    assert "unknown review_id" in t3["error"]

    t4 = await call(
        "screening_record_many",
        review_id=bad_rev,
        decisions=[{
            "paper_ref": "10.1000/1",
            "stage": "title_abstract",
            "decision": "include",
            "reviewer": "primary",
        }],
    )
    assert t4["ok"] is False
    assert "unknown review_id" in t4["error"]

    t5 = await call("screening_list", review_id=bad_rev)
    assert t5["ok"] is False
    assert "unknown review_id" in t5["error"]

    t6 = await call(
        "screening_agreement",
        review_id=bad_rev,
        stage="title_abstract",
        reviewer_a="alice",
        reviewer_b="bob",
    )
    assert t6["ok"] is False
    assert "unknown review_id" in t6["error"]

    t7 = await call("prisma_status", review_id=bad_rev)
    assert t7["ok"] is False
    assert "unknown review_id" in t7["error"]

    t8 = await call("prisma_flow_diagram", review_id=bad_rev)
    assert t8["ok"] is False
    assert "unknown review_id" in t8["error"]

    t9 = await call("prisma_checklist", review_id=bad_rev, report_text="Sample text")
    assert t9["ok"] is False
    assert "unknown review_id" in t9["error"]
