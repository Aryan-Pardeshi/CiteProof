# SPDX-License-Identifier: MIT
"""Tests for protocol_record, dedup_papers, evidence_record, evidence_table, review_status and export_bundle."""

from __future__ import annotations

import csv
import io
import json

import pytest

from tests.conftest import call

SOURCE = (
    "Methods: 240 adults were randomised. Results: all-cause mortality was lower with drug X "
    "(RR 0.82, 95% CI 0.70-0.96; n = 240). Blood pressure fell by a mean difference of −5.1 mmHg "
    "(95% CI −7.0 to −3.2)."
)


async def _review() -> str:
    return (await call("review_create", title="New tools", question="Does drug X reduce mortality?"))["review_id"]


# --- protocol_record ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_protocol_partial_updates_completeness_and_amendments() -> None:
    rid = await _review()
    first = await call(
        "protocol_record",
        review_id=rid,
        population="Adults with hypertension",
        intervention="Drug X",
        outcomes="all-cause mortality; blood pressure",
    )
    assert first["ok"] is True
    assert first["complete"] is False
    assert set(first["missing_required"]) == {"inclusion_criteria", "exclusion_criteria", "databases", "search_strategy"}
    assert first["protocol"]["outcomes"] == ["all-cause mortality", "blood pressure"]
    assert first["amendments"] == 0

    second = await call(
        "protocol_record",
        review_id=rid,
        inclusion_criteria=["RCT", "adults"],
        exclusion_criteria=["children"],
        databases=["PubMed", "Embase"],
        search_strategy="(drug X) AND (mortality)",
        rob_tool="RoB2",
        year_min=2000,
    )
    assert second["complete"] is True
    assert second["protocol"]["population"] == "Adults with hypertension"  # kept
    assert second["protocol"]["rob_tool"] == "rob2"
    assert second["protocol"]["year_min"] == 2000
    assert "year_range" not in second["missing_recommended"]
    assert second["amendments"] == 0

    read_back = await call("protocol_record", review_id=rid)
    assert read_back["updated_fields"] == []
    assert read_back["protocol"] == second["protocol"]

    amended = await call("protocol_record", review_id=rid, population="Adults aged 40+ with hypertension")
    assert amended["updated_fields"] == ["population"]
    assert amended["amendments"] == 1

    cleared = await call("protocol_record", review_id=rid, databases=[])
    assert "databases" in cleared["missing_required"]


@pytest.mark.asyncio
async def test_protocol_rejects_bad_values() -> None:
    rid = await _review()
    assert (await call("protocol_record", review_id=rid, year_min=2020, year_max=2010))["ok"] is False
    assert (await call("protocol_record", review_id=rid, population="x" * 3000))["ok"] is False
    assert (await call("protocol_record", review_id="rev-missing1"))["ok"] is False


# --- dedup_papers ------------------------------------------------------------------------------


PAPERS = [
    {"id": "a", "doi": "10.1000/ABC", "title": "Drug X and mortality in adults", "year": 2020, "authors": ["Smith, J"]},
    {"id": "b", "doi": "https://doi.org/10.1000/abc", "title": "Drug X and mortality in adults.", "year": 2020},
    {"id": "c", "pmid": "123", "title": "Another trial of drug X in adults", "year": 2019, "authors": "Jones A; Lee B"},
    {"id": "d", "pmid": "pmid:123", "title": "Another trial of drug X in adults", "year": 2019},
    {"id": "e", "title": "Blood pressure outcomes after drug X", "year": 2018, "authors": ["Müller, K"]},
    {"id": "f", "title": "Blood Pressure Outcomes after Drug X!", "year": "2018", "first_author": "Kai Muller"},
    {"id": "g", "title": "Blood pressure outcomes after drug X", "year": 2021, "authors": ["Muller, K"]},
    {"id": "h", "title": "Unrelated paper on something else", "year": 2017},
]


@pytest.mark.asyncio
async def test_dedup_papers_clusters_by_doi_pmid_and_title_year_author() -> None:
    res = await call("dedup_papers", papers=PAPERS)
    assert res["ok"] is True
    assert res["records_checked"] == 8
    assert res["duplicates_removed"] == 3
    assert res["unique_records"] == 5
    by_kept = {c["kept_id"]: c for c in res["clusters"]}
    assert set(by_kept) == {"a", "c", "e"}
    assert by_kept["a"]["matched_on"] == ["doi"]
    assert by_kept["c"]["matched_on"] == ["pmid"]
    assert by_kept["e"]["matched_on"] == ["title_year_author"]
    assert res["removed_indices"] == [1, 3, 5]
    # Same title, different year: reported, not merged
    assert res["possible_duplicates"] and res["possible_duplicates"][0]["indices"] == [4, 5, 6]
    assert res["recorded"] is False


@pytest.mark.asyncio
async def test_dedup_papers_flags_conflicting_dois_and_is_deterministic() -> None:
    papers = [
        {"doi": "10.1000/one", "pmid": "55", "title": "T one"},
        {"doi": "10.1000/two", "pmid": "55", "title": "T two"},
    ]
    first = await call("dedup_papers", papers=papers)
    second = await call("dedup_papers", papers=papers)
    assert first == second
    assert first["clusters"][0]["conflicting_dois"] is True


@pytest.mark.asyncio
async def test_dedup_papers_records_count_into_prisma() -> None:
    rid = await _review()
    await call("prisma_record_search", review_id=rid, source="PubMed", query="x", date_run="2026-01-01", hits=5)
    await call("prisma_record_search", review_id=rid, source="Embase", query="x", date_run="2026-01-01", hits=3)
    res = await call("dedup_papers", papers=PAPERS, review_id=rid)
    assert res["recorded"] is True
    assert res["warnings"] == []  # 8 records checked, 8 hits recorded
    st = await call("prisma_status", review_id=rid)
    assert st["duplicates_removed"] == 3 and st["records_after_dedup"] == 5
    assert st["dedup_method"].startswith("dedup_papers")

    short = await call("dedup_papers", papers=PAPERS[:4], review_id=rid)
    assert short["recorded"] is False
    assert any("8 hits" in w and "NOT recorded" in w for w in short["warnings"])
    # The partial call did not replace the review's dedup record
    st = await call("prisma_status", review_id=rid)
    assert st["duplicates_removed"] == 3 and st["records_after_dedup"] == 5


@pytest.mark.asyncio
async def test_dedup_papers_cannot_exceed_recorded_hits() -> None:
    rid = await _review()
    res = await call("dedup_papers", papers=PAPERS, review_id=rid)
    assert res["ok"] is False
    assert "prisma_record_search" in res["error"]


@pytest.mark.asyncio
async def test_dedup_papers_batches_never_overwrite_the_review_total() -> None:
    """Regression: a review has one dedup count; batched calls must not replace each other's result."""
    rid = await _review()
    await call("prisma_record_search", review_id=rid, source="PubMed", query="x", date_run="2026-01-01", hits=8)
    first_half, second_half = PAPERS[:4], PAPERS[4:]

    one = await call("dedup_papers", papers=first_half, review_id=rid)
    two = await call("dedup_papers", papers=second_half, review_id=rid)
    assert one["recorded"] is False and two["recorded"] is False
    assert all("prisma_record_dedup" in r["warnings"][0] for r in (one, two))
    status = await call("prisma_status", review_id=rid)
    assert status["dedup_recorded"] is False and status["duplicates_removed"] == 0

    whole = await call("dedup_papers", papers=PAPERS, review_id=rid)
    assert whole["recorded"] is True
    status = await call("prisma_status", review_id=rid)
    assert status["duplicates_removed"] == 3 and status["records_after_dedup"] == 5

    # A later partial call cannot undo the complete result
    await call("dedup_papers", papers=second_half, review_id=rid)
    status = await call("prisma_status", review_id=rid)
    assert status["duplicates_removed"] == 3


@pytest.mark.asyncio
async def test_dedup_papers_result_tells_the_agent_to_keep_removed_metadata() -> None:
    res = await call("dedup_papers", papers=PAPERS)
    assert "copy any field" in res["note"]


@pytest.mark.asyncio
async def test_dedup_papers_over_the_limit_points_to_prisma_record_dedup() -> None:
    papers = [{"title": f"Distinct title number {i} for the limit test"} for i in range(10_001)]
    res = await call("dedup_papers", papers=papers)
    assert res["ok"] is False
    assert "prisma_record_dedup" in res["error"]


# --- evidence_record / evidence_table ----------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_record_checks_quote_numbers_and_arithmetic() -> None:
    rid = await _review()
    ok = await call(
        "evidence_record",
        review_id=rid,
        paper_ref="10.1000/trial",
        outcome="All-cause mortality",
        study_design="RCT",
        sample_size=240,
        effect_measure="risk ratio",
        effect_estimate=0.82,
        ci_lower="0.70",
        ci_upper=0.96,
        quote="all-cause mortality was lower with drug X (RR 0.82, 95% CI 0.70-0.96; n = 240)",
        source_text=SOURCE,
    )
    assert ok["ok"] is True, ok
    assert ok["quote_verified"] is True
    assert ok["numbers_not_in_quote"] == []
    assert ok["effect"] == "RR 0.82 (95% CI 0.7 to 0.96)"

    md = await call(
        "evidence_record",
        review_id=rid,
        paper_ref="10.1000/trial",
        outcome="Systolic blood pressure",
        study_design="RCT",
        effect_measure="MD",
        effect_estimate=-5.1,
        ci_lower=-7.0,
        ci_upper=-3.2,
        quote="a mean difference of −5.1 mmHg (95% CI −7.0 to −3.2)",
        source_text=SOURCE,
    )
    assert md["quote_verified"] is True and md["numbers_not_in_quote"] == []
    assert md["rows_for_paper"] == 2

    wrong = await call(
        "evidence_record",
        review_id=rid,
        paper_ref="10.1000/trial",
        outcome="All-cause mortality",
        study_design="RCT",
        sample_size=250,
        effect_measure="rr",
        effect_estimate=0.82,
        ci_lower=0.70,
        ci_upper=0.96,
        quote="all-cause mortality was lower with drug X (RR 0.82, 95% CI 0.70-0.96; n = 240)",
        source_text=SOURCE,
    )
    assert wrong["ok"] is True and wrong["updated"] is True
    assert wrong["numbers_not_in_quote"] == ["sample_size"]
    assert any("sample_size" in w for w in wrong["warnings"])

    base = {"review_id": rid, "paper_ref": "10.1000/trial", "outcome": "o", "study_design": "RCT", "quote": "mortality was lower with drug X", "source_text": SOURCE}
    for extra, needle in (
        ({"effect_measure": "rr", "effect_estimate": 0.5, "ci_lower": 0.7, "ci_upper": 0.9}, "outside"),
        ({"effect_measure": "rr", "effect_estimate": 0.8, "ci_lower": 0.9, "ci_upper": 0.7}, "greater than"),
        ({"effect_measure": "or", "effect_estimate": -0.2}, "ratio"),
        ({"effect_estimate": 0.8}, "effect_measure"),
        ({"effect_measure": "rr", "effect_estimate": 0.8, "ci_lower": 0.7}, "both"),
        ({"effect_measure": "rr", "effect_estimate": "abc"}, "number"),
        ({"source_text": ""}, "source_text"),
    ):
        res = await call("evidence_record", **{**base, **extra})
        assert res["ok"] is False, extra
        assert needle in res["error"], (extra, res["error"])


@pytest.mark.asyncio
async def test_evidence_table_formats_gaps_and_csv_safety() -> None:
    rid = await _review()
    await call("screening_record_many", review_id=rid, decisions=[
        {"paper_ref": ref, "stage": stage, "decision": "include"}
        for ref in ("10.1000/trial", "10.1000/other") for stage in ("title_abstract", "full_text")
    ])
    await call(
        "evidence_record", review_id=rid, paper_ref="10.1000/trial", outcome="Mortality", study_design="RCT",
        population="=HYPERLINK(\"http://x\")", effect_measure="rr", effect_estimate=0.82, ci_lower=0.7, ci_upper=0.96,
        quote="all-cause mortality was lower with drug X (RR 0.82, 95% CI 0.70-0.96; n = 240)", source_text=SOURCE,
    )
    not_included = await call(
        "evidence_record", review_id=rid, paper_ref="pmid:999", outcome="Mortality", study_design="RCT",
        quote="mortality was lower with drug X", source_text=SOURCE,
    )
    assert any("not among the studies included" in w for w in not_included["warnings"])

    md = await call("evidence_table", review_id=rid)
    assert md["format"] == "markdown" and md["row_count"] == 2
    assert md["table"].startswith("| Study | Design |")
    assert "RR 0.82 (95% CI 0.7 to 0.96)" in md["table"]
    assert md["included_without_rows"] == ["10.1000/other"]
    assert md["included_without_rows_count"] == 1

    c = await call("evidence_table", review_id=rid, format="csv")
    rows = list(csv.DictReader(io.StringIO(c["table"])))
    assert len(rows) == 2
    trial = next(r for r in rows if r["paper_ref"] == "10.1000/trial")
    assert trial["population"].startswith("'=")
    assert trial["effect_estimate"] == "0.82"

    j = await call("evidence_table", review_id=rid, format="json", paper_ref="10.1000/TRIAL")
    assert j["row_count"] == 1 and j["rows"][0]["quote_verified"] is True


def test_csv_formula_guard_covers_full_width_prefixes() -> None:
    from citeproof_mcp.tools.extraction import _csv_safe

    for prefix in ("=", "+", "-", "@", "\t", "\r", "＝", "＋", "－", "＠"):
        assert _csv_safe(f"{prefix}SUM(A1)") == f"'{prefix}SUM(A1)", repr(prefix)
    assert _csv_safe("mortality") == "mortality"
    assert _csv_safe("") == ""
    assert _csv_safe(0.82) == 0.82
    assert _csv_safe(None) is None


# --- review_status / export_bundle ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_status_walks_through_the_phases() -> None:
    rid = await _review()
    st = await call("review_status", review_id=rid)
    assert st["ok"] is True and st["phase"] == "protocol"
    assert st["next_steps"][0].startswith("Complete the protocol")

    await call(
        "protocol_record", review_id=rid, population="Adults", intervention="Drug X", outcomes=["mortality"],
        inclusion_criteria=["RCT"], exclusion_criteria=["children"], databases=["PubMed"], search_strategy="drug X",
    )
    assert (await call("review_status", review_id=rid))["phase"] == "searching"

    await call("prisma_record_search", review_id=rid, source="PubMed", query="drug X", date_run="2026-01-01", hits=2)
    assert (await call("review_status", review_id=rid))["phase"] == "deduplication"

    await call("prisma_record_dedup", review_id=rid, duplicates_removed=0, method="none")
    assert (await call("review_status", review_id=rid))["phase"] == "title_abstract_screening"

    await call("screening_record_many", review_id=rid, decisions=[
        {"paper_ref": "10.1000/trial", "stage": "title_abstract", "decision": "include"},
        {"paper_ref": "10.1000/other", "stage": "title_abstract", "decision": "exclude", "reason": "wrong population"},
    ])
    assert (await call("review_status", review_id=rid))["phase"] == "full_text_screening"

    await call("screening_record", review_id=rid, paper_ref="10.1000/trial", stage="full_text", decision="include")
    st = await call("review_status", review_id=rid)
    assert st["phase"] == "data_extraction"
    assert st["included_studies"] == 1
    assert st["prisma"]["complete"] is True
    assert any("single-reviewer" in w or "one reviewer" in w for w in st["warnings"])

    await call(
        "evidence_record", review_id=rid, paper_ref="10.1000/trial", outcome="Mortality", study_design="RCT",
        quote="mortality was lower with drug X", source_text=SOURCE,
    )
    assert (await call("review_status", review_id=rid))["phase"] == "risk_of_bias"

    for d in ("D1", "D2", "D3", "D4", "D5"):
        await call("rob_record", review_id=rid, paper_ref="10.1000/trial", tool="rob2", domain=d, judgement="low",
                   quote="240 adults were randomised", source_text=SOURCE)
    st = await call("review_status", review_id=rid)
    assert st["phase"] == "claim_checking"
    assert st["risk_of_bias"]["counts_by_overall"] == {"low": 1}

    await call("claim_record", review_id=rid, claim_text="Drug X lowers mortality", paper_ref="10.1000/trial",
               label="supported", reason="RR below 1", quote="mortality was lower with drug X", source_text=SOURCE)
    st = await call("review_status", review_id=rid)
    # The included DOI has not been checked with verify_dois yet
    assert st["phase"] == "claim_checking"
    assert st["dois"]["included_dois_unchecked"] == 1


@pytest.mark.asyncio
async def test_export_bundle_markdown_and_json() -> None:
    rid = await _review()
    await call("protocol_record", review_id=rid, population="Adults", databases=["PubMed"])
    await call("prisma_record_search", review_id=rid, source="PubMed", query="drug X | mortality", date_run="2026-01-01", hits=2)
    await call("screening_record", review_id=rid, paper_ref="10.1000/trial", stage="title_abstract", decision="include", reviewer="alice")
    await call("screening_record", review_id=rid, paper_ref="10.1000/trial", stage="title_abstract", decision="exclude", reason="<b>x</b>", reviewer="bob")
    await call("claim_record", review_id=rid, claim_text="Drug X lowers mortality", paper_ref="10.1000/trial",
               label="supported", reason="RR below 1", quote="mortality was lower with drug X", source_text=SOURCE)

    md = await call("export_bundle", review_id=rid)
    assert md["ok"] is True and md["format"] == "markdown"
    text = md["content"]
    for heading in ("## Protocol", "## Searches", "## PRISMA 2020 flow", "## Screening decisions", "### Reviewer agreement",
                    "## Evidence table", "## Risk of bias", "## Claim checks", "## DOI checks", "## Open items"):
        assert heading in text, heading
    assert "drug X \\| mortality" in text  # pipe escaped inside the table
    assert "<b>" not in text
    # Several reviewers and no 'primary': counts are not computed, and the bundle says why
    assert "Counts not computed" in text
    assert SOURCE not in text
    assert md["suggested_filename"].endswith(".md")

    js = await call("export_bundle", review_id=rid, format="json", reviewer="alice")
    data = json.loads(js["content"])
    assert data["review"]["review_id"] == rid
    assert data["prisma"]["reviewer_used"] == "alice"
    assert data["agreement"][0]["disagreement_count"] == 1
    assert data["claims"][0]["quote_verified"] is True
    assert SOURCE not in js["content"]


@pytest.mark.asyncio
async def test_export_bundle_markdown_escapes_text_outside_tables() -> None:
    evil_title = "Trial <img src=x onerror=alert(1)>\n## Injected heading"
    created = await call("review_create", title=evil_title, question="Q <script>alert(2)</script>?")
    rid = created["review_id"]
    await call("protocol_record", review_id=rid, population="<b>adults</b>", intervention="Drug X")
    await call("protocol_record", review_id=rid, population="<i>children</i>")  # an amendment
    await call("prisma_record_search", review_id=rid, source="PubMed", query="x", date_run="2026-01-01", hits=3)
    await call("prisma_record_dedup", review_id=rid, duplicates_removed=1, method="<u>manual</u>")

    text = (await call("export_bundle", review_id=rid))["content"]
    for raw in ("<img", "<script", "<b>", "<i>", "<u>"):
        assert raw not in text, raw
    assert "\n## Injected heading" not in text  # the newline in the title cannot open a new heading
    assert text.splitlines()[0].startswith("# Review audit bundle: Trial &lt;img")
