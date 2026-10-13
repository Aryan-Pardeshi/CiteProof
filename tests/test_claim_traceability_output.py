"""Traceability of generated review claims into the final research-mode output.

Contract under test (Academic Evidence Layer, Task 1):
  Every claim rendered in the final paper prose must keep a machine-readable
  link back to its supporting evidence, and every such link must resolve the
  full chain:

      review_claim.claim_id -> evidence_id -> paper_id -> source_url/doi

  - Section writers embed inline structured markers ([EV:<evidence_id>]) after
    sentences grounded in an EvidenceRecord.
  - A deterministic claims_linker node converts those markers into a parallel
    machine-readable claims manifest (state.review_claims) attached to the
    final document output.
  - Validators enforce the structured chains, not just rendered author-year
    text. Any claim without a resolvable evidence_id fails the gates.
"""


import pytest

from backend.app.agents.research_mode.validation import (
    citation_validator_node,
    claim_validator_node,
    integrity_auditor_node,
    validate_claim_chains,
)
from backend.app.agents.research_mode.writing import (
    EV_MARKER_RE,
    claims_linker_node,
)
from backend.app.models.evidence import (
    EvidenceRecord,
    make_paper_id,
)

# ---------------------------------------------------------------------------
# Fixtures: a simulated completed research-mode corpus
# ---------------------------------------------------------------------------

PAPER_A = {
    "paper_id": make_paper_id(doi="10.1234/alpha", title="Alpha Paper"),
    "title": "Alpha Paper",
    "authors": ["Curie, M."],
    "year": "2021",
    "doi": "10.1234/alpha",
    "source_url": "https://example.org/alpha",
    "screening_status": "included",
}

PAPER_B = {
    "paper_id": make_paper_id(doi="10.1234/beta", title="Beta Paper"),
    "title": "Beta Paper",
    "authors": ["Lovelace, A."],
    "year": "2022",
    "doi": "10.1234/beta",
    "source_url": "https://example.org/beta",
    "screening_status": "included",
}

EVIDENCE = [
    {
        "evidence_id": f"{PAPER_A['paper_id']}_ev001",
        "paper_id": PAPER_A["paper_id"],
        "claim_summary": "Model reaches 94.2% top-1 accuracy on ImageNet.",
        "exact_quote": "reaches a top-1 accuracy of 94.2 percent",
        "source_section": "Results",
        "page": 2,
        "metric_name": "Accuracy",
        "reported_value": 94.2,
        "unit_or_scale": "%",
        "effect_direction": "positive",
    },
    {
        "evidence_id": f"{PAPER_B['paper_id']}_ev001",
        "paper_id": PAPER_B["paper_id"],
        "claim_summary": "Scaling data improves robustness under distribution shift.",
        "exact_quote": "robustness improves consistently with data scale",
        "source_section": "Abstract",
        "page": None,
        "effect_direction": "positive",
    },
]

PROSE = {
    "introduction": (
        "Prior work reported strong benchmark gains "
        f"[EV:{PAPER_A['paper_id']}_ev001]. "
        "This review examines whether such gains survive distribution shift."
    ),
    "literature_review": (
        "Curie et al. (2021) report state-of-the-art accuracy "
        f"[EV:{PAPER_A['paper_id']}_ev001], while Lovelace et al. (2022) "
        "connect data scale to robustness "
        f"[EV:{PAPER_B['paper_id']}_ev001]. "
        f"A sentence citing nothing at all stays unlinked. "
        f"A fabricated reference [EV:ghost_ev999] must not survive linking."
    ),
    "results": (
        "Across the included studies, reported accuracy reaches 94.2% "
        f"[EV:{PAPER_A['paper_id']}_ev001]."
    ),
    "discussion": (
        "The evidence suggests scale alone does not explain robustness "
        f"[EV:{PAPER_B['paper_id']}_ev001]."
    ),
}


def _completed_state(**overrides):
    state = {
        "problem_statement": "Robustness of deep networks",
        "paper_records": [dict(PAPER_A), dict(PAPER_B)],
        "evidence_records": [dict(e) for e in EVIDENCE],
        **{k: str(v) for k, v in PROSE.items()},
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# 1. EvidenceRecord carries an explicit page field (null when unknown)
# ---------------------------------------------------------------------------


def test_evidence_record_page_field_null_when_unknown():
    rec = EvidenceRecord(
        evidence_id="p_ev001", paper_id="p", claim_summary="Any finding."
    )
    assert rec.page is None  # explicitly null, never guessed


def test_evidence_record_page_field_round_trips():
    rec = EvidenceRecord(
        evidence_id="p_ev001", paper_id="p", claim_summary="Any finding.", page=3
    )
    assert rec.model_dump()["page"] == 3


def test_ev_marker_regex_matches_normalized_ids():
    assert EV_MARKER_RE.findall("text [EV:p123_ev001] more") == ["p123_ev001"]
    assert EV_MARKER_RE.findall("text [ev: p123_ev002 ] more") == ["p123_ev002"]
    assert EV_MARKER_RE.findall("no markers here") == []


# ---------------------------------------------------------------------------
# 2. claims_linker_node builds the machine-readable claims manifest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_linker_builds_manifest_resolving_every_marker():
    result = await claims_linker_node(_completed_state())
    claims = result["review_claims"]
    assert len(claims) >= 4

    by_text_marker = {}
    for c in claims:
        for ev_id in c["supporting_evidence_ids"]:
            by_text_marker.setdefault(ev_id, []).append(c)

    # Every well-formed marker produced a claim linking its evidence id.
    known_ids = {e["evidence_id"] for e in EVIDENCE}
    linked_ids = {eid for c in claims for eid in c["supporting_evidence_ids"]}
    assert linked_ids <= known_ids  # no fabricated links
    assert f"{PAPER_A['paper_id']}_ev001" in linked_ids
    assert f"{PAPER_B['paper_id']}_ev001" in linked_ids

    # Claim ids are scoped and unique.
    claim_ids = [c["claim_id"] for c in claims]
    assert len(claim_ids) == len(set(claim_ids))
    assert all(c["target_section"] for c in claims)


@pytest.mark.asyncio
async def test_linker_strips_unresolvable_markers_from_prose():
    result = await claims_linker_node(_completed_state())
    lit = result["literature_review"]
    assert "ghost_ev999" not in lit  # fabricated refs never reach the reader
    assert f"{PAPER_B['paper_id']}_ev001" in lit or "[EV:" not in lit


@pytest.mark.asyncio
async def test_linker_flags_quantitative_claims():
    result = await claims_linker_node(_completed_state())
    quant = [
        c for c in result["review_claims"]
        if c["target_section"] == "results"
    ]
    assert quant and any(c["is_quantitative"] for c in quant)


@pytest.mark.asyncio
async def test_linker_without_markers_yields_no_claims():
    bare = _completed_state()
    for sec in ("introduction", "literature_review", "results", "discussion"):
        bare[sec] = f"Plain prose for {sec} without any markers."
    result = await claims_linker_node(bare)
    assert result.get("review_claims") == []
    assert "unresolved_claims" in result


# ---------------------------------------------------------------------------
# 3. Full-chain validation: claim -> evidence -> paper -> source_url/doi
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_manifest_claim_resolves_full_chain():
    linked = await claims_linker_node(_completed_state())
    state = {**_completed_state(), "review_claims": linked["review_claims"]}
    total, resolved, unresolved, _details = validate_claim_chains(
        state["review_claims"], state["evidence_records"], state["paper_records"]
    )
    assert total > 0
    assert unresolved == []  # fail loudly if ANY claim lacks a resolvable chain
    assert resolved == total


def test_chain_validation_flags_ghost_evidence_id():
    claims = [{
        "claim_id": "results_cl001",
        "claim_text": "Ghosted.",
        "target_section": "results",
        "supporting_evidence_ids": ["ghost_ev999"],
    }]
    total, resolved, unresolved, details = validate_claim_chains(
        claims, EVIDENCE, [PAPER_A, PAPER_B]
    )
    assert total == 1 and resolved == 0
    assert "results_cl001" in unresolved
    assert any("ghost_ev999" in d for d in details)


def test_chain_validation_flags_empty_support_and_missing_locator():
    no_support = {
        "claim_id": "intro_cl001",
        "claim_text": "Nothing backs this.",
        "target_section": "introduction",
        "supporting_evidence_ids": [],
    }
    broken_locator = {
        "claim_id": "disc_cl001",
        "claim_text": "Backed by evidence whose paper has no URL/DOI.",
        "target_section": "discussion",
        "supporting_evidence_ids": [EVIDENCE[0]["evidence_id"]],
    }
    papers_no_locator = [{
        "paper_id": PAPER_A["paper_id"], "title": "Alpha", "doi": None, "source_url": "",
    }]
    _total, resolved, unresolved, details = validate_claim_chains(
        [no_support, broken_locator], EVIDENCE, papers_no_locator
    )
    assert resolved == 0
    assert set(unresolved) == {"intro_cl001", "disc_cl001"}
    assert any("no supporting evidence" in d.lower() for d in details)
    assert any("locator" in d.lower() for d in details)


# ---------------------------------------------------------------------------
# 4. Validators consume the structured manifest and gate the report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_citation_validator_marks_claims_resolved_on_completed_run():
    linked = await claims_linker_node(_completed_state())
    state = {**_completed_state(), "review_claims": linked["review_claims"]}
    result = await citation_validator_node(state)
    assert result.get("unresolved_claims") == []
    statuses = {c["claim_id"]: c["validation_status"] for c in result["review_claims"]}
    # A resolving chain proves the cited source exists and is locatable, nothing
    # more: no validator tests that the passage supports the sentence, so the
    # status must never read "verified".
    assert statuses and all(s == "resolved" for s in statuses.values())


@pytest.mark.asyncio
async def test_claim_validator_grounding_does_not_upgrade_status_to_verified():
    """Numeric grounding is reported in grounded_quantitative_claim_ids, not as a label."""
    linked = await claims_linker_node(_completed_state())
    state = {**_completed_state(), "review_claims": linked["review_claims"]}
    state.update(await citation_validator_node(state))
    result = await claim_validator_node(state)
    assert result["grounded_quantitative_claim_ids"]
    assert {c["validation_status"] for c in result["review_claims"]} == {"resolved"}


@pytest.mark.asyncio
async def test_claim_validator_marks_ungrounded_quantitative_claim_unsupported():
    claims = [{
        "claim_id": "results_cl001",
        "claim_text": "Accuracy reaches 99.9% on ImageNet.",
        "target_section": "results",
        "supporting_evidence_ids": [EVIDENCE[0]["evidence_id"]],
        "is_quantitative": True,
        "validation_status": "resolved",
    }]
    result = await claim_validator_node({**_completed_state(), "review_claims": claims})
    assert result["review_claims"][0]["validation_status"] == "unsupported"
    assert "results_cl001" in result["unsupported_numerical_claims"]


@pytest.mark.asyncio
async def test_integrity_auditor_fails_gates_when_chain_broken():
    linked = await claims_linker_node(_completed_state())
    claims = linked["review_claims"]
    claims[0]["supporting_evidence_ids"] = ["ghost_ev999"]  # simulate drift
    state = {**_completed_state(), "review_claims": claims}
    audited = await integrity_auditor_node(state)
    report = audited["validation_report"]
    assert report["total_review_claims"] == len(claims)
    assert report["passed_all_gates"] is False
    assert any("ghost_ev999" in u or "unresolved" in u.lower()
               for u in report["unresolved_review_claims"] + report["integrity_flags"])


@pytest.mark.asyncio
async def test_claim_validator_grounds_structured_quantitative_claims():
    linked = await claims_linker_node(_completed_state())
    state = {**_completed_state(), "review_claims": linked["review_claims"]}
    result = await claim_validator_node(state)
    grounded_ids = set(result.get("grounded_quantitative_claim_ids") or [])
    quant_ids = {
        c["claim_id"] for c in state["review_claims"] if c["is_quantitative"]
    }
    assert quant_ids
    assert quant_ids <= grounded_ids


# ---------------------------------------------------------------------------
# 5. Manifest attaches to the final document output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_appendices_render_traceability_matrix():
    linked = await claims_linker_node(_completed_state())
    state = {**_completed_state(), "review_claims": linked["review_claims"]}
    result = await import_appendices()(state)
    appendix = result["appendices"]
    assert "Traceability" in appendix
    for c in state["review_claims"]:
        assert c["claim_id"] in appendix


@pytest.mark.asyncio
async def test_appendix_states_traceability_coverage_without_overclaiming():
    """Only sentences the writer tagged with [EV:...] get a chain, so Appendix C must not claim every statement."""
    linked = await claims_linker_node(_completed_state())
    state = {**_completed_state(), "review_claims": linked["review_claims"]}
    appendix = (await import_appendices()(state))["appendices"]
    assert "for every statement" not in appendix
    assert "Untagged sentences are not linked" in appendix


@pytest.mark.asyncio
async def test_final_pipeline_state_carries_resolvable_manifest_end_to_end():
    """Integration: completed-run shape -> linker -> validators all green."""
    linked = await claims_linker_node(_completed_state())
    state = {**_completed_state(), "review_claims": linked["review_claims"]}
    cited = await citation_validator_node(state)
    state.update({k: v for k, v in cited.items() if v is not None})
    claimed = await claim_validator_node(state)
    state.update({k: v for k, v in claimed.items() if v is not None})
    audited = await integrity_auditor_node(state)
    report = audited["validation_report"]

    assert state["review_claims"], "manifest missing from final state"
    for c in state["review_claims"]:
        assert c["supporting_evidence_ids"], (
            f"claim {c['claim_id']} has no resolvable evidence_id"
        )
        for eid in c["supporting_evidence_ids"]:
            ev = next(e for e in state["evidence_records"] if e["evidence_id"] == eid)
            paper = next(
                p for p in state["paper_records"] if p["paper_id"] == ev["paper_id"]
            )
            assert paper.get("source_url") or paper.get("doi")
    assert report["resolved_review_claims"] == report["total_review_claims"]
    assert report["passed_all_gates"] is True


# ---------------------------------------------------------------------------
# 6. Graph wiring
# ---------------------------------------------------------------------------


def test_claims_linker_registered_in_graph():
    from backend.app.graph import research_mode_builder as builder_mod

    assert "claims_linker" in builder_mod.builder.nodes


def import_appendices():
    from backend.app.agents.research_mode.writing import appendices_agent

    return appendices_agent
