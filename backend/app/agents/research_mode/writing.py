"""Phase 5 Section-Specific Writers for Research Mode.

Consumes structured EvidenceRecord slices and PaperRecord collections,
enforcing strict evidence grounding and APA 7th edition citations.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage

from backend.app.agents.research_mode._common import (
    EVIDENCE_BASIS_NOTE,
    _safe_invoke_llm,
    _strip_preamble,
    get_llm_for,
)
from backend.app.models.evidence import (
    EvidenceRecord,
    PaperRecord,
    PRISMATracker,
    ReviewClaim,
    make_claim_id,
)
from backend.app.tools.academic_search import format_apa
from backend.app.tools.figures import render_evidence_table, render_prisma_diagram

logger = logging.getLogger(__name__)

# Inline structured marker embedded by writers after a sentence grounded in a
# specific EvidenceRecord: [EV:<evidence_id>]. claims_linker_node converts
# these into the machine-readable ReviewClaim manifest and strips them from
# the rendered prose.
EV_MARKER_RE = re.compile(r"\[\s*ev:\s*([A-Za-z0-9_.\-]+?)\s*\]", re.IGNORECASE)

# Prose sections scanned for evidence markers.
LINKED_PROSE_SECTIONS = ("introduction", "literature_review", "results", "discussion")


def _is_quantitative_sentence(text: str) -> bool:
    """Mirror of the validator's numeric-claim heuristic, kept dependency-light."""
    return bool(re.search(
        r"(?:\b\d+\.?\d*|\d+)\s*(?:%|percent\b|BLEU\b|F1\b|accuracy\b|points\b|ms\b|seconds\b|x\b|fold\b)",
        text,
        re.IGNORECASE,
    ))


def _strip_ev_markers(text: str) -> str:
    """Remove provenance markers before feeding prose into downstream prompts.

    Sections that do not consume the structured evidence base (discussion,
    abstract, conclusion) would otherwise copy [EV:...] tags verbatim into
    rendered text that claims_linker_node never parses.
    """
    if not text:
        return text or ""
    cleaned = EV_MARKER_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", cleaned)


async def claims_linker_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Convert inline [EV:<id>] markers into the machine-readable claims manifest.

    Deterministic post-pass over writer output:
    - Each sentence carrying resolvable markers becomes one ReviewClaim whose
      supporting_evidence_ids link back to EvidenceRecords.
    - All markers are stripped from the rendered prose (human-readable text
      keeps APA author-year citations only).
    - Markers referencing unknown evidence ids never reach the reader and are
      reported under unresolved_claims.
    """
    evidence_by_id = {
        e.get("evidence_id"): e
        for e in (state.get("evidence_records") or [])
        if e.get("evidence_id")
    }

    updates: Dict[str, Any] = {}
    review_claims: List[Dict[str, Any]] = []
    unresolved_claims: List[Dict[str, Any]] = []

    for section in LINKED_PROSE_SECTIONS:
        prose = str(state.get(section) or "")
        if not prose:
            continue

        # Pull markers that trail a sentence onto the sentence itself so a
        # marker-only fragment ("...state of the art. [EV:id]") never becomes
        # an empty claim while the real sentence loses its link.
        prose = re.sub(r"([.?!])[ \t]+(?=\[\s*ev:)", r"\1", prose, flags=re.IGNORECASE)

        sentences = re.split(r"(?<=[.?!])\s+", prose)
        pending_ids: List[str] = []
        last_claim_index: Optional[int] = None
        for sentence in sentences:
            marker_ids = EV_MARKER_RE.findall(sentence)
            if not marker_ids:
                continue
            resolved_ids: List[str] = []
            for mid in marker_ids:
                if mid in evidence_by_id:
                    if mid not in resolved_ids:
                        resolved_ids.append(mid)
                else:
                    unresolved_claims.append({
                        "section": section,
                        "evidence_id": mid,
                        "reason": "unknown evidence_id",
                    })

            cleaned = re.sub(r"\s{2,}", " ", EV_MARKER_RE.sub("", sentence)).strip()
            if not cleaned:
                # Marker-only fragment: defer its ids to the adjacent claim
                # sentence instead of emitting an empty claim_text.
                pending_ids.extend(i for i in resolved_ids if i not in pending_ids)
                continue

            supporting = pending_ids + [i for i in resolved_ids if i not in pending_ids]
            pending_ids = []
            if supporting:
                review_claims.append(ReviewClaim(
                    claim_id=make_claim_id(section, len(review_claims) + 1),
                    claim_text=cleaned,
                    target_section=section,
                    supporting_evidence_ids=supporting,
                    is_quantitative=_is_quantitative_sentence(cleaned),
                    validation_status="pending",
                ).model_dump())
                last_claim_index = len(review_claims) - 1

        if pending_ids:
            if last_claim_index is not None:
                target_ids = review_claims[last_claim_index]["supporting_evidence_ids"]
                target_ids.extend(i for i in pending_ids if i not in target_ids)
            else:
                for mid in pending_ids:
                    unresolved_claims.append({
                        "section": section,
                        "evidence_id": mid,
                        "reason": "marker not adjacent to any claim sentence",
                    })

        updates[section] = re.sub(r"[ \t]{2,}", " ", EV_MARKER_RE.sub("", prose)).strip()

    logger.info(
        f"claims_linker: built {len(review_claims)} review claims, "
        f"{len(unresolved_claims)} unresolved markers."
    )
    updates["review_claims"] = review_claims
    updates["unresolved_claims"] = unresolved_claims
    return updates


def _format_evidence_context(state: Dict[str, Any], max_items: int = 12) -> str:
    """Format structured evidence records into Markdown context for writers."""
    ev_dicts = state.get("evidence_records") or []
    if not ev_dicts:
        # Fallback to papers summary if evidence records not yet populated
        paper_dicts = state.get("paper_records") or state.get("screened_papers") or []
        lines = []
        for p in paper_dicts[:10]:
            raw_authors = p.get("authors") or ["Author"]
            authors_str = ", ".join(raw_authors[:3]) + (" et al." if len(raw_authors) > 3 else "")
            year = p.get("year", "n.d.")
            lines.append(f"[{authors_str} ({year})] {p.get('title')}: {(p.get('abstract') or '')[:300]}")
        return "\n\n".join(lines)

    lines = []
    for e in ev_dicts[:max_items]:
        lines.append(
            f"Evidence ID: {e.get('evidence_id')}\n"
            f"Claim: {e.get('claim_summary')}\n"
            f"Exact Quote: \"{e.get('exact_quote')}\" (Source: {e.get('source_section')})\n"
            f"Metric: {e.get('metric_name')} = {e.get('reported_value')} (Baseline: {e.get('baseline_value')})\n"
            f"Effect: {e.get('effect_direction')}"
        )
    return "\n---\n".join(lines)


async def literature_review_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Writer 1: Writes exhaustive, thematic Literature Review grounded in evidence records."""
    ps = state.get("problem_statement", "")
    taxonomy = state.get("taxonomy") or {}
    evidence_ctx = _format_evidence_context(state, max_items=15)
    papers = state.get("paper_records") or state.get("screened_papers") or []
    
    # Generate reference citation hints
    citation_hints = "\n".join(
        f"- {format_apa(p)}" for p in papers[:15]
    )

    llm = get_llm_for("aggregator", state, temperature=0.3)
    prompt = f"""You are a Leading Academic Scholar.
Write an exhaustive, highly structured, critical Literature Review for:
Topic: {ps}

Structured Evidence Base:
{evidence_ctx}

Available Primary Literature for Citation:
{citation_hints}

Instructions:
1. Organize the literature review into 3-4 major thematic sections corresponding to the evidence base.
2. In-text citations MUST strictly use APA format e.g. (Author et al., Year) or Author (Year).
3. Critically analyze conflicting methodologies and compare quantitative benchmark results.
4. TRACEABILITY: immediately after any sentence grounded in a specific evidence record, append its machine tag on the same line, e.g. "...establishes a new state of the art [EV:<Evidence ID>]". Use the exact Evidence ID from the context above.
{EVIDENCE_BASIS_NOTE}
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    return {"literature_review": _strip_preamble(raw)}


async def research_design_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Writer 2: Formulates proposed research design (explicitly framed as proposed future study)."""
    ps = state.get("problem_statement", "")
    hypotheses = state.get("hypotheses", [])
    llm = get_llm_for("planner", state, temperature=0.2)

    prompt = f"""You are an Expert Research Methodologist.
Propose a comprehensive Research Design & Experimental Methodology to empirically test the formulated hypotheses for:
Topic: {ps}

Hypotheses:
{chr(10).join(f"- {h}" for h in hypotheses)}

Structure the section in Markdown:
- 1. Methodological Philosophy & Research Paradigm
- 2. Experimental Architecture & Target Testbeds
- 3. Variable Operationalization & Control Protocols
{EVIDENCE_BASIS_NOTE}
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    return {"research_design": _strip_preamble(raw)}


async def data_collection_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Writer 3: Formulates proposed data collection protocol & instruments."""
    ps = state.get("problem_statement", "")
    design = state.get("research_design", "")
    llm = get_llm_for("planner", state, temperature=0.2)

    prompt = f"""You are a Data Engineering and Sampling Methodologist.
Detail the proposed Data Collection Protocol for:
Topic: {ps}

Research Design Context:
{design[:800]}

Detail:
- Sampling Strategy & Dataset Selection
- Data Ingestion, Extraction, and Preprocessing Pipelines
- Ethical Considerations & Data Integrity Safeguards
{EVIDENCE_BASIS_NOTE}
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    return {"data_collection_plan": _strip_preamble(raw)}


async def data_analysis_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Writer 4: Formulates proposed statistical analysis pipeline."""
    ps = state.get("problem_statement", "")
    hypotheses = state.get("hypotheses", [])
    llm = get_llm_for("planner", state, temperature=0.2)

    prompt = f"""You are a Senior Quantitative Biostatistician and Data Scientist.
Specify the proposed Statistical and Empirical Analysis Plan for testing:
Hypotheses:
{chr(10).join(f"- {h}" for h in hypotheses)}

Detail:
- Primary Statistical Tests (e.g. ANOVA, Regression, Ablation Benchmarks)
- Significance Thresholds, Confidence Intervals, and Power Analysis
- Model Evaluation Metrics and Error Analysis Protocols
{EVIDENCE_BASIS_NOTE}
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    return {"data_analysis_plan": _strip_preamble(raw)}


async def results_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Writer 5: Synthesizes empirical evidence and maps findings against formulated hypotheses."""
    ps = state.get("problem_statement", "")
    hypotheses = state.get("hypotheses", [])
    evidence_ctx = _format_evidence_context(state, max_items=15)
    llm = get_llm_for("aggregator", state, temperature=0.2)

    prompt = f"""You are a Principal Empirical Research Analyst.
Synthesize the empirical findings from the literature base and map them against the formulated hypotheses for:
Topic: {ps}

Formulated Hypotheses:
{chr(10).join(f"- {h}" for h in hypotheses)}

Extracted Evidence Records & Benchmark Metrics:
{evidence_ctx}

Provide a detailed Results Section in Markdown:
- 1. Overview of Included Evidence Base
- 2. Hypothesis-by-Hypothesis Empirical Synthesis (evaluate whether current literature supports, refutes, or partially confirms H1..H5)
- 3. Quantitative Benchmark Comparisons (tabulate or summarize reported baseline vs state-of-the-art metrics)
4. TRACEABILITY: immediately after any sentence grounded in a specific evidence record, append its machine tag on the same line, e.g. "...reaches 94.2% accuracy [EV:<Evidence ID>]". Use the exact Evidence ID from the context above.
{EVIDENCE_BASIS_NOTE}
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    return {"results": _strip_preamble(raw)}


async def discussion_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Writer 6: Synthesizes theoretical & practical implications."""
    ps = state.get("problem_statement", "")
    results = _strip_ev_markers(state.get("results", ""))
    llm = get_llm_for("aggregator", state, temperature=0.3)

    prompt = f"""You are a Senior Academic Scholar and Discussion Lead.
Write an in-depth Discussion and Implications section for:
Topic: {ps}

Synthesized Results:
{results[:1000]}

Provide:
- Discussion of Findings & Theoretical Convergence
- Theoretical Implications (contributions to foundational models and theories)
- Practical & Industrial Implications (actionable guidance for practitioners and engineers)
{EVIDENCE_BASIS_NOTE}
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    content = _strip_preamble(raw)
    return {
        "discussion": content,
        "implications": "Detailed theoretical and practical implications are discussed in the Discussion section."
    }


async def limitations_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Writer 7: Evaluates limitations across the evidence base and review methodology."""
    ps = state.get("problem_statement", "")
    ev_dicts = state.get("evidence_records") or []
    llm = get_llm_for("researcher", state, temperature=0.2)

    ev_lims = [f"- {e.get('claim_summary')}: {e.get('limitations')}" for e in ev_dicts if e.get("limitations")]
    ev_lims_text = "\n".join(ev_lims[:8]) if ev_lims else "General empirical boundary constraints in literature."

    prompt = f"""You are a Critical Research Reviewer.
Write a rigorous, transparent Limitations section for:
Topic: {ps}

Extracted Evidence Caveats:
{ev_lims_text}

Address:
- Methodological Limitations in Current Literature
- Data Availability, Generalizability, and Compute Constraints
- Systematic Review Scope & Search Boundary Limitations
{EVIDENCE_BASIS_NOTE}
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    return {"limitations": _strip_preamble(raw)}


async def conclusion_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Writer 8: Formulates authoritative conclusion summarizing research contributions."""
    ps = state.get("problem_statement", "")
    results = _strip_ev_markers(state.get("results", ""))
    llm = get_llm_for("aggregator", state, temperature=0.2)

    prompt = f"""You are a Lead Research Author.
Write an authoritative Academic Conclusion (3 paragraphs) summarizing the core discoveries, synthesis, and takeaways for:
Topic: {ps}

Synthesized Results Findings:
{results[:800]}
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    return {"conclusion": _strip_preamble(raw)}


async def future_scope_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Writer 9: Outlines future research directions."""
    ps = state.get("problem_statement", "")
    gaps = state.get("research_gap", "")
    llm = get_llm_for("planner", state, temperature=0.3)

    prompt = f"""You are an Academic Visionary.
Formulate 4-5 high-impact Future Research Directions for:
Topic: {ps}

Identified Gaps:
{gaps}

Return ONLY a bulleted list of 4-5 items.
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    # Strip only list markers (bullets and "1." / "1)" enumerators) so legitimate
    # leading text such as a year ("2026 benchmarks ...") is preserved.
    lines = []
    for line in raw.split("\n"):
        cleaned = re.sub(r"^\s*(?:[-*•]+|\d{1,2}[.)])\s+", "", line).strip()
        if len(cleaned) > 10:
            lines.append(cleaned)
    # Return an empty list when nothing usable was extracted so the renderer can
    # omit the section instead of showing generic filler.
    return {"future_scope": lines[:5]}


async def references_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Writer 10: Formats APA 7th edition bibliography from included paper records."""
    paper_dicts = state.get("paper_records") or state.get("screened_papers") or []
    references = [format_apa(p) for p in paper_dicts if p.get("screening_status") == "included" or not p.get("screening_status")]
    if not references:
        references = [format_apa(p) for p in paper_dicts[:15]]
    references = sorted(list(set(references)), key=lambda r: r.lower())
    return {"references": references}


async def appendices_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Writer 11: Compiles search protocol, evidence tables, and claim traceability matrix."""
    protocol = state.get("search_protocol") or {}
    tracker = state.get("prisma_tracker") or {}
    review_claims = state.get("review_claims") or []
    evidence_by_id = {
        e.get("evidence_id"): e
        for e in (state.get("evidence_records") or [])
        if e.get("evidence_id")
    }

    inc = protocol.get('inclusion_criteria') or []
    exc = protocol.get('exclusion_criteria') or []
    inc_str = ", ".join(inc) if inc else "Peer-reviewed empirical studies"
    exc_str = ", ".join(exc) if exc else "Non-empirical articles and duplicates"

    app_text = f"""## Appendix A: Systematic Search Protocol
- **Population / Domain**: {protocol.get('population', 'N/A')}
- **Intervention**: {protocol.get('intervention', 'N/A')}
- **Inclusion Criteria**: {inc_str}
- **Exclusion Criteria**: {exc_str}

## Appendix B: PRISMA Selection Audit
- Total Records Identified: {tracker.get('records_identified', 0)}
- Duplicates Removed: {tracker.get('duplicates_removed', 0)}
- Records Screened: {tracker.get('records_screened', 0)}
- Studies Included: {tracker.get('studies_included', 0)}
"""

    if review_claims:
        rows = []
        for c in review_claims:
            locations = []
            for eid in c.get("supporting_evidence_ids") or []:
                ev = evidence_by_id.get(eid) or {}
                loc = str(ev.get("section") or "unknown")
                if ev.get("page") is not None:
                    loc += f" p. {ev['page']}"
                locator = ev.get("doi") or ev.get("source_url") or ""
                target = f"https://doi.org/{locator}" if locator and not str(locator).startswith("http") else locator
                link = f" [source]({target})" if target else ""
                locations.append(f"{eid} ({loc}){link}")
            rows.append(
                f"- **{c.get('claim_id')}** ({c.get('target_section', '')}): "
                f"{str(c.get('claim_text') or '')[:140]} -> {'; '.join(locations) if locations else 'unlinked'}"
            )
        app_text += "\n## Appendix C: Claim Traceability Matrix\n"
        app_text += (
            "Machine-readable claim -> evidence -> source chains for each sentence the writer tagged with "
            "evidence (introduction, literature review, results and discussion). Untagged sentences are not linked.\n"
        )
        app_text += "\n".join(rows) + "\n"

    return {"appendices": app_text}


async def introduction_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Writer 12: Synthesizes comprehensive Introduction with problem context and contributions."""
    ps = state.get("problem_statement", "")
    objs = state.get("research_objectives", [])
    llm = get_llm_for("aggregator", state, temperature=0.3)

    prompt = f"""You are a Lead Academic Author.
Write a publication-grade Introduction section for:
Topic: {ps}

Objectives:
{chr(10).join(f"- {o}" for o in objs)}

Structure:
- Context & Scientific Motivation
- Problem Articulation & Significance
- Outline of Research Objectives & Paper Structure
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    return {"introduction": _strip_preamble(raw)}


async def abstract_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Writer 13: Synthesizes structured abstract."""
    ps = state.get("problem_statement", "")
    results = _strip_ev_markers(state.get("results", ""))
    conclusion = _strip_ev_markers(state.get("conclusion", ""))
    llm = get_llm_for("aggregator", state, temperature=0.2)

    prompt = f"""You are a Lead Academic Author.
Write a structured Academic Abstract (250-300 words) summarizing:
- Background & Problem Motivation: {ps}
- Methodology & Synthesis: Structured multi-source evidence extraction and PRISMA 2020 protocol
- Principal Findings: {results[:500]}
- Significance: {conclusion[:300]}
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    return {"abstract": _strip_preamble(raw)}


async def title_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Writer 14: Finalizes concise academic title."""
    ps = state.get("problem_statement", "")
    llm = get_llm_for("planner", state, temperature=0.2)

    prompt = f"""You are a Scholarly Editor.
Generate a concise, authoritative academic research paper title for:
Topic: {ps}

Return ONLY the single title string without quotes.
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    title = _strip_preamble(raw).strip().strip('"').rstrip(".")
    return {
        "title": title or ps,
        "status": "completed"
    }


async def figures_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Renders PRISMA flow chart and evidence mapping figures."""
    figures_dir = os.getenv("FIGURES_DIR", "./data/figures")
    thread_id = state.get("thread_id", "default")
    safe_thread_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(thread_id))
    resolved_base = Path(figures_dir).resolve()
    out_dir = (resolved_base / safe_thread_id).resolve()
    if not str(out_dir).startswith(str(resolved_base)):
        out_dir = resolved_base / "default"
    out_dir.mkdir(parents=True, exist_ok=True)

    prisma_path = str(out_dir / "prisma.png")
    evidence_path = str(out_dir / "evidence.png")

    fig_map: Dict[str, str] = {}
    try:
        # Prefer the deterministic PRISMA tracker; fall back to raw corpus_stats.
        # Pass the dict through as-is so render_prisma_diagram can filter the
        # fields it supports instead of us discarding it with a key guard.
        raw_tr = state.get("prisma_tracker") or state.get("corpus_stats") or {}
        tracker_arg = raw_tr if isinstance(raw_tr, dict) else PRISMATracker()
        p_res = render_prisma_diagram(tracker_arg, prisma_path)
        if p_res and os.path.exists(p_res):
            fig_map["prisma"] = p_res
    except Exception as e:
        logger.warning(f"PRISMA diagram rendering failed: {e}")

    try:
        papers = state.get("paper_records") or state.get("screened_papers") or []
        hyps = state.get("hypotheses") or []
        e_res = render_evidence_table(papers, hyps, evidence_path)
        if e_res and os.path.exists(e_res):
            fig_map["evidence"] = e_res
    except Exception as e:
        logger.warning(f"Evidence table rendering failed: {e}")

    return {"figures": fig_map}
