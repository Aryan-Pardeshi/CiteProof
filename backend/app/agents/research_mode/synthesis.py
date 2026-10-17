"""Phase 5 Synthesis & Theoretical Framing Agents for Research Mode."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List
from langchain_core.messages import HumanMessage

from backend.app.agents.research_mode._common import (
    get_llm_for,
    _safe_invoke_llm,
    _strip_preamble,
)
from backend.app.models.evidence import TaxonomyTheme

logger = logging.getLogger(__name__)


async def taxonomy_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 16: Synthesizes evidence records into a multi-dimensional thematic taxonomy."""
    ev_dicts = state.get("evidence_records") or []
    ps = state.get("problem_statement", "")
    llm = get_llm_for("planner", state, temperature=0.2)

    ev_summaries = "\n".join(f"- {e.get('claim_summary')}" for e in ev_dicts[:12])

    prompt = f"""You are a Principal Academic Synthesizer.
Classify the following evidence items into 3-4 structured thematic categories or taxonomy dimensions for:
Problem Statement: {ps}

Evidence Claims:
{ev_summaries}

Return a valid JSON array with schema:
[
  {{
    "theme_id": "theme_1",
    "theme_name": "Name of Theme (e.g. Architectural Innovations)",
    "description": "2-3 sentence overview of this research dimension",
    "subthemes": ["Subtopic A", "Subtopic B"]
  }}
]
"""
    try:
        raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
        clean = raw
        if "```" in clean:
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        themes_data = json.loads(clean.strip())
        # Reject mappings, strings, and any other JSON value: the prompt schema is
        # an array of theme objects.
        if not isinstance(themes_data, list) or not all(isinstance(t, dict) for t in themes_data):
            raise ValueError(f"taxonomy payload is not a list of theme objects (got {type(themes_data).__name__})")
        if not themes_data:
            raise ValueError("taxonomy payload contained no themes")
        return {"taxonomy": {"themes": themes_data}}
    except Exception as e:
        logger.warning(f"taxonomy_agent JSON parse defaulted: {e}")
        default_themes = [
            {"theme_id": "theme_1", "theme_name": "Foundational Architectures", "description": "Core algorithmic and structural foundations.", "subthemes": []},
            {"theme_id": "theme_2", "theme_name": "Empirical Performance & Benchmarking", "description": "Quantitative benchmark evaluation across datasets.", "subthemes": []},
            {"theme_id": "theme_3", "theme_name": "Operational Constraints & Scalability", "description": "Resource efficiency, throughput, and deployment challenges.", "subthemes": []}
        ]
        return {"taxonomy": {"themes": default_themes}}


async def gap_analysis_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 17: Synthesizes empirical contradictions and identifies critical research gaps."""
    ps = state.get("problem_statement", "")
    ev_dicts = state.get("evidence_records") or []
    taxonomy = state.get("taxonomy") or {}
    llm = get_llm_for("researcher", state, temperature=0.2)

    ev_text = "\n".join(f"- {e.get('claim_summary')} (Limitations: {e.get('limitations')})" for e in ev_dicts[:10])

    prompt = f"""You are a Critical Literature Analyst.
Identify the major research gaps, conflicting findings, and methodological limitations across the evidence base for:
Topic: {ps}

Evidence Base:
{ev_text}

Provide a comprehensive, highly rigorous academic synthesis of the Research Gaps (3-4 paragraphs in Markdown format).
Include specific subsections:
- 1. Methodological Gaps
- 2. Empirical Inconsistencies & Contradictions
- 3. Unaddressed Boundary Conditions
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    return {"research_gap": _strip_preamble(raw)}


async def conceptual_framework_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 18: Constructs theoretical framework mapping independent, dependent, and moderating constructs."""
    ps = state.get("problem_statement", "")
    gap = state.get("research_gap", "")
    llm = get_llm_for("planner", state, temperature=0.2)

    prompt = f"""You are a Senior Theoretical Framework Architect.
Construct a formal Academic Conceptual Framework addressing the identified research gaps for:
Problem Statement: {ps}

Identified Gaps:
{gap[:800]}

Provide a detailed Conceptual Framework in Markdown format including:
- Core Theoretical Constructs (Independent, Dependent, Mediating, and Moderating Variables)
- Structural Relationships & Mechanistic Pathways
- Theoretical Grounding (linking constructs to established scientific paradigms)
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    return {"conceptual_framework": _strip_preamble(raw)}


async def hypotheses_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 19: Formulates directional, testable hypotheses H1..H5 grounded in extracted evidence."""
    ps = state.get("problem_statement", "")
    framework = state.get("conceptual_framework", "")
    llm = get_llm_for("researcher", state, temperature=0.2)

    prompt = f"""You are an Expert Hypothesis Formulator.
Formulate 4-5 rigorous, testable, directional academic hypotheses (H1 to H5) for:
Problem Statement: {ps}

Conceptual Framework Context:
{framework[:1000]}

Return a valid JSON array of strings:
[
  "H1: Detailed directional hypothesis statement...",
  "H2: Detailed directional hypothesis statement...",
  "H3: Detailed directional hypothesis statement...",
  "H4: Detailed directional hypothesis statement...",
  "H5: Detailed directional hypothesis statement..."
]
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    hyp_list: List[str] = []
    try:
        clean = raw
        if "```" in clean:
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        parsed = json.loads(clean.strip())
        if not isinstance(parsed, list):
            raise ValueError(f"hypotheses payload is not a list (got {type(parsed).__name__})")
        # Normalize object entries to their text value; anything else is rejected
        # so downstream writers always receive a list of strings.
        normalized: List[str] = []
        for item in parsed:
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = str(
                    item.get("hypothesis")
                    or item.get("text")
                    or item.get("statement")
                    or item.get("description")
                    or ""
                ).strip()
            else:
                text = ""
            if text:
                normalized.append(text)
        if not normalized:
            raise ValueError("hypotheses payload contained no usable statements")
        hyp_list = normalized
    except Exception:
        # Fallback regex extraction of H1..H5
        lines = [line.strip() for line in raw.split("\n") if line.strip().startswith(("H1", "H2", "H3", "H4", "H5", "1.", "2.", "3.", "4.", "5."))]
        hyp_list = lines if lines else [
            f"H1: Architectural optimization improves accuracy across domain tasks in {ps[:40]}.",
            f"H2: Scaling parameter capacity yields non-linear performance gains.",
            f"H3: Multi-source data synthesis reduces empirical error rates."
        ]

    # Copy papers cleanly without injecting synthetic modulo ratings
    paper_dicts = state.get("paper_records") or []
    copied_papers = [dict(p) for p in paper_dicts]

    return {
        "hypotheses": hyp_list,
        "paper_records": copied_papers,
        "hitl_checkpoint": "checkpoint_3",
        "status": "awaiting_approval"
    }
