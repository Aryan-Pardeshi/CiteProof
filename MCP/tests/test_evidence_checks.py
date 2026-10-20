# SPDX-License-Identifier: MIT
"""Tests for claim-support records and risk-of-bias tools."""

from __future__ import annotations

import hashlib

import pytest
from citeproof_mcp import store
from citeproof_mcp.tools.evidence_checks import ROBINS_I_DOMAINS
from citeproof_mcp.tools.quotes import quote_in_source

from tests.conftest import call

# ============================================================================
# quote_in_source unit tests
# ============================================================================


def test_quote_in_source_exact_match():
    quote = "patients receiving drug x experienced improvement"
    source = "In this trial, patients receiving drug x experienced improvement compared to placebo."
    assert quote_in_source(quote, source) is True


def test_quote_in_source_case_and_whitespace():
    quote = "PATIENTS   RECEIVING\nDRUG X   experienced\t\timprovement"
    source = "patients receiving drug x experienced improvement in symptoms."
    assert quote_in_source(quote, source) is True


def test_quote_in_source_curly_quotes_and_en_dash():
    quote = "“drug–induced” ‘remission’"
    source = 'A state of "drug-induced" \'remission\' was noted in 80% of patients.'
    assert quote_in_source(quote, source) is True


def test_quote_in_source_line_break_hyphenation():
    # Hyphenation in quote
    quote1 = "demonstrated signifi-\ncant improvement"
    source1 = "The active cohort demonstrated significant improvement over 12 weeks."
    assert quote_in_source(quote1, source1) is True

    # Hyphenation at line break in source text
    quote2 = "demonstrated significant improvement"
    source2 = "The active cohort demonstrated signifi-\ncant improvement over 12 weeks."
    assert quote_in_source(quote2, source2) is True

    # Windows CRLF line endings
    quote3 = "demonstrated significant improvement"
    source3 = "The active cohort demonstrated signifi-\r\ncant improvement over 12 weeks."
    assert quote_in_source(quote3, source3) is True


def test_quote_in_source_ellipsis_in_order_and_out_of_order():
    source = "All patients receiving drug x in the active cohort experienced significant improvement."

    quote_in_order = "patients receiving drug x ... experienced significant improvement"
    assert quote_in_source(quote_in_order, source) is True

    quote_out_of_order = "experienced significant improvement ... patients receiving drug x"
    assert quote_in_source(quote_out_of_order, source) is False


def test_quote_in_source_shorter_than_12_chars():
    # Quotes shorter than 12 normalised characters are never verified
    quote = "short quote"  # 11 characters
    source = "This paper has a short quote inside it."
    assert quote_in_source(quote, source) is False

    # Ellipsis where every part is shorter than 12 characters
    quote_ellipsis_short = "short ... tiny ... brief"
    source2 = "This is short and tiny and brief in text."
    assert quote_in_source(quote_ellipsis_short, source2) is False


def test_quote_in_source_empty_and_not_in_source():
    assert quote_in_source("", "Some source text that is long enough.") is False
    assert quote_in_source("   ", "Some source text that is long enough.") is False

    quote = "this sentence is completely absent from the source text"
    source = "This text discusses a totally different medical topic."
    assert quote_in_source(quote, source) is False


# ============================================================================
# claim_record tests
# ============================================================================


@pytest.mark.asyncio
async def test_claim_record_each_label():
    create_res = await call("review_create", title="Claim Labels Review", question="Q1")
    review_id = create_res["review_id"]
    source = "Patients given treatment A showed complete remission after six weeks."

    # 'supported' with verified quote
    res_sup = await call(
        "claim_record",
        review_id=review_id,
        claim_text="Treatment A causes complete remission",
        paper_ref="10.1001/jama.2021.0001",
        source_text=source,
        quote="complete remission after six weeks",
        label="supported",
        reason="Stated directly in trial results",
    )
    assert res_sup["ok"] is True
    assert res_sup["quote_verified"] is True
    assert res_sup["updated"] is False

    # 'partial' with verified quote
    res_part = await call(
        "claim_record",
        review_id=review_id,
        claim_text="Treatment A works for everyone",
        paper_ref="10.1001/jama.2021.0001",
        source_text=source,
        quote="complete remission after six weeks",
        label="partial",
        reason="Only tested in six-week cohort",
    )
    assert res_part["ok"] is True
    assert res_part["quote_verified"] is True

    # 'unsupported' with empty quote
    res_unsup = await call(
        "claim_record",
        review_id=review_id,
        claim_text="Treatment A causes zero side effects",
        paper_ref="10.1001/jama.2021.0001",
        source_text=source,
        quote="",
        label="unsupported",
        reason="Adverse events were not recorded",
    )
    assert res_unsup["ok"] is True
    assert res_unsup["quote_verified"] is False


@pytest.mark.asyncio
async def test_claim_record_upsert_flag():
    create_res = await call("review_create", title="Upsert Review", question="Q2")
    review_id = create_res["review_id"]
    source = "Patients given treatment B showed complete remission after six weeks."

    # First record: updated=False
    res1 = await call(
        "claim_record",
        review_id=review_id,
        claim_text="Claim B text",
        paper_ref="10.1001/jama.2021.0002",
        source_text=source,
        quote="complete remission after six weeks",
        label="supported",
        reason="Initial reason",
    )
    assert res1["ok"] is True
    assert res1["updated"] is False

    # Same claim_key and paper_ref: updated=True
    res2 = await call(
        "claim_record",
        review_id=review_id,
        claim_text="Claim B text",
        paper_ref="10.1001/jama.2021.0002",
        source_text=source,
        quote="complete remission after six weeks",
        label="supported",
        reason="Updated rationale",
    )
    assert res2["ok"] is True
    assert res2["updated"] is True


@pytest.mark.asyncio
async def test_claim_record_missing_quote_rejected_for_supported_and_partial():
    create_res = await call("review_create", title="Missing Quote Review", question="Q3")
    review_id = create_res["review_id"]

    res_sup = await call(
        "claim_record",
        review_id=review_id,
        claim_text="Claim without quote",
        paper_ref="10.1001/jama.2021.0003",
        source_text="Some source text",
        quote="",
        label="supported",
        reason="Some reason",
    )
    assert res_sup["ok"] is False
    assert "quote is required" in res_sup["error"].lower()

    res_part = await call(
        "claim_record",
        review_id=review_id,
        claim_text="Partial claim without quote",
        paper_ref="10.1001/jama.2021.0003",
        source_text="Some source text",
        quote="",
        label="partial",
        reason="Some reason",
    )
    assert res_part["ok"] is False
    assert "quote is required" in res_part["error"].lower()


@pytest.mark.asyncio
async def test_claim_record_unverified_quote_warning():
    create_res = await call("review_create", title="Unverified Warning Review", question="Q4")
    review_id = create_res["review_id"]

    res = await call(
        "claim_record",
        review_id=review_id,
        claim_text="Claim with absent quote",
        paper_ref="10.1001/jama.2021.0004",
        source_text="This source text does not have the cited quote anywhere.",
        quote="sentence not found in the source text at all",
        label="supported",
        reason="Agent claim",
    )
    assert res["ok"] is True
    assert res["quote_verified"] is False
    assert len(res["warnings"]) > 0
    assert "not found in the supplied source text" in res["warnings"][0].lower()


@pytest.mark.asyncio
async def test_claim_record_bad_label():
    create_res = await call("review_create", title="Bad Label Review", question="Q5")
    review_id = create_res["review_id"]

    res = await call(
        "claim_record",
        review_id=review_id,
        claim_text="Claim with invalid label",
        paper_ref="10.1001/jama.2021.0005",
        source_text="Some text",
        quote="some quote text",
        label="confirmed",
        reason="Invalid label test",
    )
    assert res["ok"] is False
    assert "label must be one of" in res["error"]


@pytest.mark.asyncio
async def test_claim_record_oversize_source_rejected():
    create_res = await call("review_create", title="Oversize Source Review", question="Q6")
    review_id = create_res["review_id"]

    oversize_text = "a" * 400001
    res = await call(
        "claim_record",
        review_id=review_id,
        claim_text="Claim with huge source",
        paper_ref="10.1001/jama.2021.0006",
        source_text=oversize_text,
        quote="some valid quote text",
        label="supported",
        reason="Reason",
    )
    assert res["ok"] is False
    assert "limit is 400000" in res["error"]


@pytest.mark.asyncio
async def test_claim_record_no_full_source_text_in_database():
    create_res = await call("review_create", title="No Source In DB Review", question="Q7")
    review_id = create_res["review_id"]
    source = "Patients receiving treatment G demonstrated clear therapeutic response."

    await call(
        "claim_record",
        review_id=review_id,
        claim_text="Claim G",
        paper_ref="10.1001/jama.2021.0007",
        source_text=source,
        quote="demonstrated clear therapeutic response",
        label="supported",
        reason="Stated in results",
    )

    conn = store.open_review(review_id)
    try:
        col_rows = conn.execute("PRAGMA table_info(claims)").fetchall()
        col_names = [col["name"] for col in col_rows]
        assert "source_text" not in col_names
        assert "source_chars" in col_names
        assert "source_sha256" in col_names

        row = conn.execute(
            "SELECT * FROM claims WHERE paper_ref = '10.1001/jama.2021.0007'"
        ).fetchone()
        assert row["source_chars"] == len(source)
        assert row["source_sha256"] == hashlib.sha256(source.encode("utf-8")).hexdigest()
    finally:
        conn.close()


# ============================================================================
# claim_report tests
# ============================================================================


@pytest.mark.asyncio
async def test_claim_report_counts_and_suspicious():
    create_res = await call("review_create", title="Report Counts Review", question="Q8")
    review_id = create_res["review_id"]
    source = "This is verified text containing target finding and details."

    # 1 supported verified
    await call(
        "claim_record",
        review_id=review_id,
        claim_text="Claim 1 supported verified",
        paper_ref="title:p1|2020",
        source_text=source,
        quote="target finding and details",
        label="supported",
        reason="R1",
    )
    # 1 supported unverified (suspicious)
    await call(
        "claim_record",
        review_id=review_id,
        claim_text="Claim 2 supported unverified",
        paper_ref="title:p2|2020",
        source_text=source,
        quote="nonexistent text in document",
        label="supported",
        reason="R2",
    )
    # 1 partial unverified (suspicious)
    await call(
        "claim_record",
        review_id=review_id,
        claim_text="Claim 3 partial unverified",
        paper_ref="title:p3|2020",
        source_text=source,
        quote="another missing quote in text",
        label="partial",
        reason="R3",
    )
    # 1 unsupported
    await call(
        "claim_record",
        review_id=review_id,
        claim_text="Claim 4 unsupported",
        paper_ref="title:p4|2020",
        source_text="",
        quote="",
        label="unsupported",
        reason="R4",
    )

    report = await call("claim_report", review_id=review_id)
    assert report["ok"] is True
    assert report["total"] == 4
    assert report["counts_by_label"]["supported"] == 2
    assert report["counts_by_label"]["partial"] == 1
    assert report["counts_by_label"]["unsupported"] == 1
    assert report["quotes_not_verified"] == 2
    assert len(report["suspicious"]) == 2
    assert len(report["unsupported"]) == 1

    suspicious_claim = report["suspicious"][0]
    assert "claim_text" in suspicious_claim
    assert "paper_ref" in suspicious_claim
    assert "reason" in suspicious_claim

    assert (
        report["method"]
        == "Labels were assigned by the calling agent. The server checked only that "
        "each quote appears in the supplied source text. This is not independent verification."
    )
    assert report["truncated"] is False


@pytest.mark.asyncio
async def test_claim_report_truncation_flag():
    create_res = await call("review_create", title="Report Truncation Review", question="Q9")
    review_id = create_res["review_id"]

    conn = store.open_review(review_id)
    try:
        for i in range(205):
            conn.execute(
                """
                INSERT INTO claims (
                    claim_key, claim_text, paper_ref, label, reason,
                    quote, quote_verified, source_chars, source_sha256, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"key_{i}",
                    f"Unsupported claim text {i}",
                    f"paper_{i}",
                    "unsupported",
                    "Reason",
                    "",
                    0,
                    0,
                    "",
                    "2026-01-01",
                ),
            )
        conn.commit()
    finally:
        conn.close()

    report = await call("claim_report", review_id=review_id)
    assert report["ok"] is True
    # Lists are capped at 200 (spec); the full count is still reported
    assert len(report["unsupported"]) == 200
    assert report["unsupported_count"] == 205
    assert report["truncated"] is True


# ============================================================================
# rob_record tests
# ============================================================================


@pytest.mark.asyncio
async def test_rob_record_valid_and_invalid_domains_and_judgements():
    create_res = await call("review_create", title="RoB Validation Review", question="Q10")
    review_id = create_res["review_id"]
    source = "Random sequence generation was performed using computer algorithm."
    quote = "random sequence generation was performed"

    # RoB 2: valid
    res_rob2 = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p1|2020",
        tool="rob2",
        domain="randomisation_process",
        judgement="low",
        quote=quote,
        source_text=source,
    )
    assert res_rob2["ok"] is True

    # RoB 2: invalid domain
    res_rob2_bad_dom = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p1|2020",
        tool="rob2",
        domain="confounding",
        judgement="low",
        quote=quote,
        source_text=source,
    )
    assert res_rob2_bad_dom["ok"] is False
    assert "invalid for tool 'rob2'" in res_rob2_bad_dom["error"]

    # RoB 2: invalid judgement
    res_rob2_bad_jud = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p1|2020",
        tool="rob2",
        domain="randomisation_process",
        judgement="critical",
        quote=quote,
        source_text=source,
    )
    assert res_rob2_bad_jud["ok"] is False
    assert "invalid for tool 'rob2'" in res_rob2_bad_jud["error"]

    # ROBINS-I: valid
    res_robins = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p2|2020",
        tool="robins_i",
        domain="confounding",
        judgement="moderate",
        quote=quote,
        source_text=source,
    )
    assert res_robins["ok"] is True

    # ROBINS-I: invalid domain
    res_robins_bad_dom = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p2|2020",
        tool="robins_i",
        domain="randomisation_process",
        judgement="moderate",
        quote=quote,
        source_text=source,
    )
    assert res_robins_bad_dom["ok"] is False

    # ROBINS-I: invalid judgement
    res_robins_bad_jud = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p2|2020",
        tool="robins_i",
        domain="confounding",
        judgement="some_concerns",
        quote=quote,
        source_text=source,
    )
    assert res_robins_bad_jud["ok"] is False

    # NOS: valid
    res_nos = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p3|2020",
        tool="nos",
        domain="selection",
        judgement="3",
        quote=quote,
        source_text=source,
    )
    assert res_nos["ok"] is True

    # NOS: invalid domain
    res_nos_bad_dom = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p3|2020",
        tool="nos",
        domain="confounding",
        judgement="2",
        quote=quote,
        source_text=source,
    )
    assert res_nos_bad_dom["ok"] is False

    # NOS: invalid judgement (out of range)
    res_nos_bad_jud = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p3|2020",
        tool="nos",
        domain="comparability",
        judgement="3",
        quote=quote,
        source_text=source,
    )
    assert res_nos_bad_jud["ok"] is False
    assert "invalid for NOS domain 'comparability'" in res_nos_bad_jud["error"]


@pytest.mark.asyncio
async def test_rob_record_mixed_tools_rejected():
    create_res = await call("review_create", title="Mixed Tools Review", question="Q11")
    review_id = create_res["review_id"]
    source = "Random sequence generation was performed using computer algorithm."
    quote = "random sequence generation was performed"

    # First record under rob2
    await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:paper mix|2020",
        tool="rob2",
        domain="randomisation_process",
        judgement="low",
        quote=quote,
        source_text=source,
    )

    # Attempt to record under nos for same paper
    res = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:paper mix|2020",
        tool="nos",
        domain="selection",
        judgement="3",
        quote=quote,
        source_text=source,
    )
    assert res["ok"] is False
    assert "already has assessments using tool 'rob2'" in res["error"]


@pytest.mark.asyncio
async def test_rob_record_star_ranges_nos():
    create_res = await call("review_create", title="NOS Star Ranges Review", question="Q12")
    review_id = create_res["review_id"]
    source = "A valid source text for testing star counts in NOS domain checks."
    quote = "valid source text for testing star counts"

    # Selection: 0 to 4 allowed; 5 rejected
    res_sel_ok = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p nos|2020",
        tool="nos",
        domain="selection",
        judgement="4",
        quote=quote,
        source_text=source,
    )
    assert res_sel_ok["ok"] is True

    res_sel_bad = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p nos|2020",
        tool="nos",
        domain="selection",
        judgement="5",
        quote=quote,
        source_text=source,
    )
    assert res_sel_bad["ok"] is False

    # Comparability: 0 to 2 allowed; 3 rejected
    res_comp_ok = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p nos|2020",
        tool="nos",
        domain="comparability",
        judgement="2",
        quote=quote,
        source_text=source,
    )
    assert res_comp_ok["ok"] is True

    res_comp_bad = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p nos|2020",
        tool="nos",
        domain="comparability",
        judgement="3",
        quote=quote,
        source_text=source,
    )
    assert res_comp_bad["ok"] is False

    # Outcome: 0 to 3 allowed; 4 rejected
    res_out_ok = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p nos|2020",
        tool="nos",
        domain="outcome",
        judgement="3",
        quote=quote,
        source_text=source,
    )
    assert res_out_ok["ok"] is True

    res_out_bad = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p nos|2020",
        tool="nos",
        domain="outcome",
        judgement="4",
        quote=quote,
        source_text=source,
    )
    assert res_out_bad["ok"] is False


@pytest.mark.asyncio
async def test_rob_record_quote_required_rules():
    create_res = await call("review_create", title="Quote Required Review", question="Q13")
    review_id = create_res["review_id"]
    source = "Some source text"

    # rob2 requires quote
    res_rob2 = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p q1|2020",
        tool="rob2",
        domain="randomisation_process",
        judgement="low",
        quote="",
        source_text=source,
    )
    assert res_rob2["ok"] is False
    assert "quote is required" in res_rob2["error"].lower()

    # robins_i requires quote for low, moderate, serious, critical
    res_robins_low = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p q2|2020",
        tool="robins_i",
        domain="confounding",
        judgement="low",
        quote="",
        source_text=source,
    )
    assert res_robins_low["ok"] is False
    assert "quote is required" in res_robins_low["error"].lower()

    # robins_i does NOT require quote for no_information
    res_robins_noinfo = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p q2|2020",
        tool="robins_i",
        domain="confounding",
        judgement="no_information",
        quote="",
        source_text=source,
    )
    assert res_robins_noinfo["ok"] is True

    # nos requires quote for star counts > 0
    res_nos_one = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p q3|2020",
        tool="nos",
        domain="selection",
        judgement="1",
        quote="",
        source_text=source,
    )
    assert res_nos_one["ok"] is False
    assert "quote is required" in res_nos_one["error"].lower()

    # nos does NOT require quote for "0"
    res_nos_zero = await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p q3|2020",
        tool="nos",
        domain="selection",
        judgement="0",
        quote="",
        source_text=source,
    )
    assert res_nos_zero["ok"] is True


# ============================================================================
# rob_summary tests
# ============================================================================


@pytest.mark.asyncio
async def test_rob_summary_rob2_worked_example():
    create_res = await call("review_create", title="RoB2 Worked Example", question="Q14")
    review_id = create_res["review_id"]
    source = (
        "Random sequence generation was sound, deviations from intended interventions were minimal, "
        "missing outcome data was addressed, measurement of the outcome was blinded, "
        "and selection of the reported result was pre-specified."
    )
    quote = "random sequence generation was sound"

    # Paper 1: one high gives high
    for d, j in [
        ("randomisation_process", "high"),
        ("deviations_from_intended_interventions", "low"),
        ("missing_outcome_data", "low"),
        ("measurement_of_the_outcome", "low"),
        ("selection_of_the_reported_result", "low"),
    ]:
        await call(
            "rob_record",
            review_id=review_id,
            paper_ref="title:p rob2 high|2020",
            tool="rob2",
            domain=d,
            judgement=j,
            quote=quote,
            source_text=source,
        )

    # Paper 2: one some_concerns gives some_concerns (no high)
    for d, j in [
        ("randomisation_process", "some_concerns"),
        ("deviations_from_intended_interventions", "low"),
        ("missing_outcome_data", "low"),
        ("measurement_of_the_outcome", "low"),
        ("selection_of_the_reported_result", "low"),
    ]:
        await call(
            "rob_record",
            review_id=review_id,
            paper_ref="title:p rob2 sc|2020",
            tool="rob2",
            domain=d,
            judgement=j,
            quote=quote,
            source_text=source,
        )

    # Paper 3: all low gives low
    for d, j in [
        ("randomisation_process", "low"),
        ("deviations_from_intended_interventions", "low"),
        ("missing_outcome_data", "low"),
        ("measurement_of_the_outcome", "low"),
        ("selection_of_the_reported_result", "low"),
    ]:
        await call(
            "rob_record",
            review_id=review_id,
            paper_ref="title:p rob2 low|2020",
            tool="rob2",
            domain=d,
            judgement=j,
            quote=quote,
            source_text=source,
        )

    # Paper 4: one domain missing gives incomplete
    for d, j in [
        ("randomisation_process", "low"),
        ("deviations_from_intended_interventions", "low"),
        ("missing_outcome_data", "low"),
        ("measurement_of_the_outcome", "low"),
    ]:
        await call(
            "rob_record",
            review_id=review_id,
            paper_ref="title:p rob2 inc|2020",
            tool="rob2",
            domain=d,
            judgement=j,
            quote=quote,
            source_text=source,
        )

    summary = await call("rob_summary", review_id=review_id)
    assert summary["ok"] is True
    papers_by_ref = {p["paper_ref"]: p for p in summary["papers"]}

    assert papers_by_ref["title:p rob2 high|2020"]["overall"] == "high"
    assert papers_by_ref["title:p rob2 sc|2020"]["overall"] == "some_concerns"
    assert papers_by_ref["title:p rob2 low|2020"]["overall"] == "low"
    assert papers_by_ref["title:p rob2 inc|2020"]["overall"] == "incomplete"
    assert papers_by_ref["title:p rob2 inc|2020"]["missing_domains"] == [
        "selection_of_the_reported_result"
    ]


@pytest.mark.asyncio
async def test_rob_summary_robins_i_worst_domain_and_no_information():
    create_res = await call("review_create", title="ROBINS-I Summary Review", question="Q15")
    review_id = create_res["review_id"]
    source = "Observational cohort study reporting interventions, outcomes, and confounding variables."
    quote = "observational cohort study reporting"

    # Paper 1: worst rated domain (critical > serious > moderate > low)
    for d, j in [
        ("confounding", "critical"),
        ("selection_of_participants", "moderate"),
        ("classification_of_interventions", "moderate"),
        ("deviations_from_intended_interventions", "moderate"),
        ("missing_data", "moderate"),
        ("measurement_of_outcomes", "moderate"),
        ("selection_of_the_reported_result", "moderate"),
    ]:
        await call(
            "rob_record",
            review_id=review_id,
            paper_ref="title:p robins crit|2020",
            tool="robins_i",
            domain=d,
            judgement=j,
            quote=quote,
            source_text=source,
        )

    # Paper 2: 5 moderate, 2 no_information -> no_information by the ROBINS-I guidance (no clear
    # indication of serious/critical risk, but information lacking); the reviewer may override (below)
    for d, j in [
        ("confounding", "no_information"),
        ("selection_of_participants", "moderate"),
        ("classification_of_interventions", "moderate"),
        ("deviations_from_intended_interventions", "moderate"),
        ("missing_data", "moderate"),
        ("measurement_of_outcomes", "moderate"),
        ("selection_of_the_reported_result", "no_information"),
    ]:
        q = "" if j == "no_information" else quote
        await call(
            "rob_record",
            review_id=review_id,
            paper_ref="title:p robins mod|2020",
            tool="robins_i",
            domain=d,
            judgement=j,
            quote=q,
            source_text=source,
        )

    # Paper 3: all 7 domains no_information -> no_information
    for d in ROBINS_I_DOMAINS:
        await call(
            "rob_record",
            review_id=review_id,
            paper_ref="title:p robins all no info|2020",
            tool="robins_i",
            domain=d,
            judgement="no_information",
            quote="",
            source_text="",
        )

    # Paper 4: 6 domains recorded -> incomplete
    for d in ROBINS_I_DOMAINS[:6]:
        await call(
            "rob_record",
            review_id=review_id,
            paper_ref="title:p robins inc|2020",
            tool="robins_i",
            domain=d,
            judgement="low",
            quote=quote,
            source_text=source,
        )

    summary = await call("rob_summary", review_id=review_id)
    assert summary["ok"] is True
    papers_by_ref = {p["paper_ref"]: p for p in summary["papers"]}

    assert papers_by_ref["title:p robins crit|2020"]["overall"] == "critical"
    assert papers_by_ref["title:p robins mod|2020"]["overall"] == "no_information"
    assert papers_by_ref["title:p robins mod|2020"]["notes"]
    assert set(papers_by_ref["title:p robins mod|2020"]["no_information_domains"]) == {
        "confounding",
        "selection_of_the_reported_result",
    }
    assert papers_by_ref["title:p robins all no info|2020"]["overall"] == "no_information"
    assert len(papers_by_ref["title:p robins all no info|2020"]["no_information_domains"]) == 7
    assert papers_by_ref["title:p robins inc|2020"]["overall"] == "incomplete"
    assert papers_by_ref["title:p robins inc|2020"]["missing_domains"] == [
        "selection_of_the_reported_result"
    ]


@pytest.mark.asyncio
async def test_rob_summary_nos_examples_with_arithmetic_in_comment():
    # Newcastle-Ottawa Scale (NOS cohort version) AHRQ conversion arithmetic:
    # Domains and star thresholds:
    #   - selection: 0 to 4 stars
    #   - comparability: 0 to 2 stars
    #   - outcome: 0 to 3 stars
    #
    # Rules:
    #   1. "good": selection in (3, 4) AND comparability in (1, 2) AND outcome in (2, 3)
    #      Example 1: selection=3, comparability=1, outcome=2
    #      Arithmetic: 3 >= 3, 1 >= 1, 2 >= 2 -> "good". total_stars = 3 + 1 + 2 = 6.
    #   2. "fair": selection == 2 AND comparability in (1, 2) AND outcome in (2, 3)
    #      Example 2: selection=2, comparability=1, outcome=2
    #      Arithmetic: 2 == 2, 1 >= 1, 2 >= 2 -> "fair". total_stars = 2 + 1 + 2 = 5.
    #   3. "poor": otherwise (complete assessment meeting neither good nor fair)
    #      Example 3: selection=1, comparability=1, outcome=2
    #      Arithmetic: selection=1 (< 2), so fails both good and fair -> "poor". total_stars = 1 + 1 + 2 = 4.
    #   4. "incomplete": any domain is missing
    #      Example 4: selection=4, outcome=3, comparability missing -> "incomplete". total_stars = 4 + 3 = 7.

    create_res = await call("review_create", title="NOS Summary Review", question="Q16")
    review_id = create_res["review_id"]
    source = "Cohort study evaluating exposure and outcomes with statistical controls."
    quote = "cohort study evaluating exposure"

    # Example 1: Good (selection 3, comparability 1, outcome 2)
    await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p nos good|2020",
        tool="nos",
        domain="selection",
        judgement="3",
        quote=quote,
        source_text=source,
    )
    await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p nos good|2020",
        tool="nos",
        domain="comparability",
        judgement="1",
        quote=quote,
        source_text=source,
    )
    await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p nos good|2020",
        tool="nos",
        domain="outcome",
        judgement="2",
        quote=quote,
        source_text=source,
    )

    # Example 2: Fair (selection 2, comparability 1, outcome 2)
    await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p nos fair|2020",
        tool="nos",
        domain="selection",
        judgement="2",
        quote=quote,
        source_text=source,
    )
    await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p nos fair|2020",
        tool="nos",
        domain="comparability",
        judgement="1",
        quote=quote,
        source_text=source,
    )
    await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p nos fair|2020",
        tool="nos",
        domain="outcome",
        judgement="2",
        quote=quote,
        source_text=source,
    )

    # Example 3: Poor (selection 1, comparability 1, outcome 2)
    await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p nos poor|2020",
        tool="nos",
        domain="selection",
        judgement="1",
        quote=quote,
        source_text=source,
    )
    await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p nos poor|2020",
        tool="nos",
        domain="comparability",
        judgement="1",
        quote=quote,
        source_text=source,
    )
    await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p nos poor|2020",
        tool="nos",
        domain="outcome",
        judgement="2",
        quote=quote,
        source_text=source,
    )

    # Example 4: Incomplete (selection 4, outcome 3; comparability missing)
    await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p nos inc|2020",
        tool="nos",
        domain="selection",
        judgement="4",
        quote=quote,
        source_text=source,
    )
    await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:p nos inc|2020",
        tool="nos",
        domain="outcome",
        judgement="3",
        quote=quote,
        source_text=source,
    )

    summary = await call("rob_summary", review_id=review_id)
    assert summary["ok"] is True
    papers_by_ref = {p["paper_ref"]: p for p in summary["papers"]}

    assert papers_by_ref["title:p nos good|2020"]["overall"] == "good"
    assert papers_by_ref["title:p nos good|2020"]["total_stars"] == 6

    assert papers_by_ref["title:p nos fair|2020"]["overall"] == "fair"
    assert papers_by_ref["title:p nos fair|2020"]["total_stars"] == 5

    assert papers_by_ref["title:p nos poor|2020"]["overall"] == "poor"
    assert papers_by_ref["title:p nos poor|2020"]["total_stars"] == 4

    assert papers_by_ref["title:p nos inc|2020"]["overall"] == "incomplete"
    assert papers_by_ref["title:p nos inc|2020"]["total_stars"] == 7
    assert papers_by_ref["title:p nos inc|2020"]["missing_domains"] == ["comparability"]


@pytest.mark.asyncio
async def test_rob_summary_table_rows_and_unverified_quotes():
    create_res = await call("review_create", title="Table Rows Review", question="Q17")
    review_id = create_res["review_id"]
    source = "Random sequence generation was performed using computer algorithm."

    # Paper 1: verified quote
    await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:paper verified|2020",
        tool="rob2",
        domain="randomisation_process",
        judgement="low",
        quote="random sequence generation was performed",
        source_text=source,
    )

    # Paper 2: unverified quote
    await call(
        "rob_record",
        review_id=review_id,
        paper_ref="title:paper unverified|2020",
        tool="rob2",
        domain="randomisation_process",
        judgement="high",
        quote="nonexistent sequence generation text",
        source_text=source,
    )

    summary = await call("rob_summary", review_id=review_id)
    assert summary["ok"] is True
    assert "table" in summary
    assert len(summary["table"]) == 2

    row_v = next(r for r in summary["table"] if r["paper_ref"] == "title:paper verified|2020")
    assert row_v["randomisation_process"] == "low"
    assert row_v["overall"] == "incomplete"

    assert "title:paper unverified|2020" in summary["papers_with_unverified_quotes"]
    assert "title:paper verified|2020" not in summary["papers_with_unverified_quotes"]


# ============================================================================
# Unknown review_id tests
# ============================================================================


@pytest.mark.asyncio
async def test_unknown_review_id_returns_ok_false_for_all_tools():
    unknown_id = "rev-00000000"

    res_claim_rec = await call(
        "claim_record",
        review_id=unknown_id,
        claim_text="Some claim text",
        paper_ref="10.1001/jama.2021.0001",
        source_text="Some source text",
        quote="some source text that is long",
        label="supported",
        reason="Reason",
    )
    assert res_claim_rec["ok"] is False
    assert "error" in res_claim_rec

    res_claim_rep = await call("claim_report", review_id=unknown_id)
    assert res_claim_rep["ok"] is False
    assert "error" in res_claim_rep

    res_rob_rec = await call(
        "rob_record",
        review_id=unknown_id,
        paper_ref="10.1001/jama.2021.0001",
        tool="rob2",
        domain="randomisation_process",
        judgement="low",
        quote="randomisation quote",
        source_text="Some source text",
    )
    assert res_rob_rec["ok"] is False
    assert "error" in res_rob_rec

    res_rob_sum = await call("rob_summary", review_id=unknown_id)
    assert res_rob_sum["ok"] is False
    assert "error" in res_rob_sum
