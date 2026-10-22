# SPDX-License-Identifier: MIT
"""Tests for source text tracking and drift detection across review calls."""

import sqlite3
import pytest
from tests.conftest import call
from citeproof_mcp import store


@pytest.mark.asyncio
async def test_source_tracking_evidence_record():
    rev = await call("review_create", title="Test Review", question="Test question?")
    review_id = rev["review_id"]

    paper = "10.1234/test.paper"
    source_v1 = "In this trial with 100 participants, mortality was 10% (10/100)."
    quote = "mortality was 10%"

    # 1. First time record is seen: no warning, no source_changed
    r1 = await call(
        "evidence_record",
        review_id=review_id,
        paper_ref=paper,
        outcome="mortality",
        study_design="RCT",
        quote=quote,
        source_text=source_v1,
    )
    assert r1["ok"]
    assert "source_changed" not in r1
    assert not any("source_text differs" in w for w in r1["warnings"])

    # 2. Same record, identical text: no warning
    r2 = await call(
        "evidence_record",
        review_id=review_id,
        paper_ref=paper,
        outcome="mortality",
        study_design="RCT",
        quote=quote,
        source_text=source_v1,
    )
    assert r2["ok"]
    assert "source_changed" not in r2
    assert not any("source_text differs" in w for w in r2["warnings"])

    # 3. Same record, different text: warning plus source_changed
    source_v2 = "In this randomized trial with 100 participants, mortality was 10% (10/100) after 1 year."
    r3 = await call(
        "evidence_record",
        review_id=review_id,
        paper_ref=paper,
        outcome="mortality",
        study_design="RCT",
        quote=quote,
        source_text=source_v2,
    )
    assert r3["ok"]
    assert "source_changed" in r3
    sc = r3["source_changed"]
    assert sc["previous_length"] == len(source_v1)
    assert sc["current_length"] == len(source_v2)
    assert sc["formatting_only"] is False
    assert any("source_text differs" in w for w in r3["warnings"])

    # 4. Same record, whitespace-only difference: warning with formatting_only = true
    source_v3 = "In this randomized trial with 100 participants,  mortality was 10% (10/100)  after 1 year."
    r4 = await call(
        "evidence_record",
        review_id=review_id,
        paper_ref=paper,
        outcome="mortality",
        study_design="RCT",
        quote=quote,
        source_text=source_v3,
    )
    assert r4["ok"]
    assert "source_changed" in r4
    sc4 = r4["source_changed"]
    assert sc4["formatting_only"] is True
    assert any("whitespace or formatting only" in w for w in r4["warnings"])


@pytest.mark.asyncio
async def test_different_records_different_excerpts():
    rev = await call("review_create", title="Test Review", question="Test question?")
    review_id = rev["review_id"]
    paper = "10.1234/test.paper"

    # 5. Two different records of one paper (different outcomes) with different excerpts:
    # no warning; review_status reports 2 distinct source texts for that paper.
    source_results = "Results section: All-cause mortality was 5% in the intervention group."
    source_methods = "Methods section: Randomisation was conducted using computer-generated sequences."

    r1 = await call(
        "evidence_record",
        review_id=review_id,
        paper_ref=paper,
        outcome="mortality",
        study_design="RCT",
        quote="mortality was 5%",
        source_text=source_results,
    )
    assert r1["ok"]
    assert "source_changed" not in r1

    r2 = await call(
        "evidence_record",
        review_id=review_id,
        paper_ref=paper,
        outcome="side effects",
        study_design="RCT",
        quote="Randomisation was conducted",
        source_text=source_methods,
    )
    assert r2["ok"]
    assert "source_changed" not in r2

    status = await call("review_status", review_id=review_id)
    assert status["ok"]
    assert status["source_texts"]["distinct_by_paper"].get(paper) == 2


@pytest.mark.asyncio
async def test_claim_record_and_rob_record_changed_text():
    rev = await call("review_create", title="Test Review", question="Test question?")
    review_id = rev["review_id"]
    paper = "10.1234/test.paper"

    # 6. Repeat changed-text test for claim_record and rob_record
    c_source1 = "Claim text version 1 supporting sentence."
    c_source2 = "Claim text version 2 modified sentence supporting sentence."

    c1 = await call(
        "claim_record",
        review_id=review_id,
        claim_text="Intervention reduces risk",
        paper_ref=paper,
        label="supported",
        reason="Supported by trial data",
        quote="supporting sentence",
        source_text=c_source1,
    )
    assert c1["ok"]
    assert "source_changed" not in c1

    c2 = await call(
        "claim_record",
        review_id=review_id,
        claim_text="Intervention reduces risk",
        paper_ref=paper,
        label="supported",
        reason="Supported by trial data",
        quote="supporting sentence",
        source_text=c_source2,
    )
    assert c2["ok"]
    assert "source_changed" in c2
    assert c2["source_changed"]["previous_length"] == len(c_source1)
    assert c2["source_changed"]["current_length"] == len(c_source2)

    # rob_record
    rob_source1 = "Participants were randomly assigned using a hidden computer sequence."
    rob_source2 = "Participants were randomly assigned using a hidden computer sequence and sealed envelopes."

    rb1 = await call(
        "rob_record",
        review_id=review_id,
        paper_ref=paper,
        tool="rob2",
        domain="randomisation_process",
        judgement="low",
        quote="randomly assigned using a hidden computer sequence",
        source_text=rob_source1,
    )
    assert rb1["ok"]
    assert "source_changed" not in rb1

    rb2 = await call(
        "rob_record",
        review_id=review_id,
        paper_ref=paper,
        tool="rob2",
        domain="randomisation_process",
        judgement="low",
        quote="randomly assigned using a hidden computer sequence",
        source_text=rob_source2,
    )
    assert rb2["ok"]
    assert "source_changed" in rb2
    assert rb2["source_changed"]["previous_length"] == len(rob_source1)
    assert rb2["source_changed"]["current_length"] == len(rob_source2)


@pytest.mark.asyncio
async def test_empty_source_text_claim_unsupported():
    rev = await call("review_create", title="Test Review", question="Test question?")
    review_id = rev["review_id"]
    paper = "10.1234/test.paper"

    # 7. Empty source_text on a claim with label "unsupported": no observation, no warning
    c = await call(
        "claim_record",
        review_id=review_id,
        claim_text="Intervention cures disease",
        paper_ref=paper,
        label="unsupported",
        reason="No data reported in the paper",
        source_text="",
        quote="",
    )
    assert c["ok"]
    assert "source_changed" not in c
    assert not any("source_text differs" in w for w in c["warnings"])

    status = await call("review_status", review_id=review_id)
    assert status["source_texts"]["changes_count"] == 0
    assert status["source_texts"]["distinct_by_paper"] == {}


@pytest.mark.asyncio
async def test_source_changes_append_only():
    rev = await call("review_create", title="Test Review", question="Test question?")
    review_id = rev["review_id"]
    paper = "10.1234/test.paper"

    # 8. source_changes keeps every change in order across three successive different texts (append-only)
    t1 = "Source text initial version 1"
    t2 = "Source text second version 22"
    t3 = "Source text third version 333"

    await call("evidence_record", review_id=review_id, paper_ref=paper, outcome="o1", study_design="RCT", quote="Source text initial", source_text=t1)
    r2 = await call("evidence_record", review_id=review_id, paper_ref=paper, outcome="o1", study_design="RCT", quote="Source text second", source_text=t2)
    r3 = await call("evidence_record", review_id=review_id, paper_ref=paper, outcome="o1", study_design="RCT", quote="Source text third", source_text=t3)

    assert r2["source_changed"]["previous_length"] == len(t1)
    assert r2["source_changed"]["current_length"] == len(t2)

    assert r3["source_changed"]["previous_length"] == len(t2)
    assert r3["source_changed"]["current_length"] == len(t3)

    bundle = await call("export_bundle", review_id=review_id, format="json")
    changes = bundle["content"]["source_changes"] if isinstance(bundle["content"], dict) else None
    if isinstance(bundle["content"], str):
        import json
        changes = json.loads(bundle["content"])["source_changes"]

    assert len(changes) == 2
    assert changes[0]["previous_length"] == len(t1)
    assert changes[0]["current_length"] == len(t2)
    assert changes[1]["previous_length"] == len(t2)
    assert changes[1]["current_length"] == len(t3)


@pytest.mark.asyncio
async def test_review_status_and_export_bundle_contain_changes():
    rev = await call("review_create", title="Test Review", question="Test question?")
    review_id = rev["review_id"]
    paper = "10.1234/test.paper"
    secret_text1 = "SUPER_SECRET_SOURCE_TEXT_V1_abc123"
    secret_text2 = "SUPER_SECRET_SOURCE_TEXT_V2_def456"

    await call("evidence_record", review_id=review_id, paper_ref=paper, outcome="o1", study_design="RCT", quote="SUPER_SECRET_SOURCE", source_text=secret_text1)
    await call("evidence_record", review_id=review_id, paper_ref=paper, outcome="o1", study_design="RCT", quote="SUPER_SECRET_SOURCE", source_text=secret_text2)

    # 9. review_status and export_bundle include the change, and export_bundle never contains source text
    status = await call("review_status", review_id=review_id)
    assert status["source_texts"]["changes_count"] == 1
    assert any("Source text changed for evidence_record" in w for w in status["warnings"])

    bundle_json = await call("export_bundle", review_id=review_id, format="json")
    content_str = bundle_json["content"]
    assert secret_text1 not in content_str
    assert secret_text2 not in content_str

    bundle_md = await call("export_bundle", review_id=review_id, format="markdown")
    md_str = bundle_md["content"]
    assert "## Source text changes" in md_str
    assert secret_text1 not in md_str
    assert secret_text2 not in md_str


@pytest.mark.asyncio
async def test_old_database_migration():
    # 10. A review database created before this change (without the new tables) opens and works
    rev = await call("review_create", title="Old Review", question="Old question?")
    review_id = rev["review_id"]

    db_path = store._db_path(review_id)
    # Drop the new tables manually to simulate an old db file
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE IF EXISTS source_observations")
    conn.execute("DROP TABLE IF EXISTS source_changes")
    conn.commit()
    conn.close()

    # Re-open through store / tool call (schema registry should apply CREATE TABLE IF NOT EXISTS)
    status = await call("review_status", review_id=review_id)
    assert status["ok"]
    assert status["source_texts"]["changes_count"] == 0

    # Test that recording evidence works seamlessly on upgraded db
    r = await call(
        "evidence_record",
        review_id=review_id,
        paper_ref="10.1234/old.paper",
        outcome="outcome1",
        study_design="RCT",
        quote="sample quote text",
        source_text="sample quote text in source text",
    )
    assert r["ok"]
    assert "source_changed" not in r
