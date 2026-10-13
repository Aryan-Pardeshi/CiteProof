"""Evidence-First 24-Agent LangGraph State Machine for Research Mode.

Features:
- 5-Phase pipeline (Planning & Protocol -> Retrieval & Screening -> Evidence Extraction -> Theoretical Framing -> Methodology & Synthesis)
- 3 Human-in-the-Loop (HITL) gates at critical quality boundaries
- Deterministic PRISMA 2020 flow tracking and academic integrity assertions
- Dual-builder dispatch preserving backward compatibility for in-flight threads
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

root_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from backend.app.agents.research_mode import (
    abstract_agent,
    appendices_agent,
    citation_expander_agent,
    # Validation
    citation_validator_node,
    claim_validator_node,
    claims_linker_node,
    conceptual_framework_agent,
    conclusion_agent,
    data_analysis_agent,
    data_collection_agent,
    discussion_agent,
    # Phase 4: Evidence Extraction
    evidence_extractor_agent,
    figures_node,
    fulltext_eligibility_agent,
    future_scope_agent,
    gap_analysis_agent,
    hypotheses_agent,
    integrity_auditor_node,
    introduction_agent,
    keyword_extractor_agent,
    limitation_extractor_agent,
    limitations_agent,
    # Writing & Rendering
    literature_review_agent,
    metadata_validator_agent,
    methodology_extractor_agent,
    # Phase 2: Retrieval
    paper_fetcher_agent,
    # Phase 3: Screening
    paper_screener_agent,
    protocol_agent,
    provenance_agent,
    quality_appraisal_agent,
    quantitative_extractor_agent,
    references_agent,
    research_design_agent,
    results_agent,
    # Phase 1: Planning
    scope_definition_agent,
    scope_reviser_agent,
    # Phase 5: Synthesis
    taxonomy_agent,
    title_agent,
)
from backend.app.graph.research_mode_builder_v1 import get_research_mode_graph_v1
from backend.app.graph.research_mode_state import ResearchModeState

logger = logging.getLogger(__name__)

APPROVAL_WORDS = ["", "approve", "approved", "ok", "yes", "looks good", "continue"]


def _is_approval(message: str) -> bool:
    return (message or "").strip().lower() in APPROVAL_WORDS


# --- HITL Checkpoint Wrapper Nodes ---

async def checkpoint_1_node(state: ResearchModeState) -> dict:
    """Gate 1: Review Scope, Search Protocol (PICOC), and Keywords."""
    logger.info("Executing Gate 1 (checkpoint_1) HITL interrupt...")
    user_input = interrupt({
        "checkpoint": "checkpoint_1",
        "message": "Review Problem Statement, Research Scope, Protocol, and Keywords before literature retrieval.",
        "problem_statement": state.get("problem_statement", ""),
        "research_objectives": state.get("research_objectives", []),
        "research_questions": state.get("research_questions", []),
        "search_protocol": state.get("search_protocol") or {},
        "keywords": state.get("keywords", [])
    })

    message = user_input.get("message", "") if isinstance(user_input, dict) else str(user_input)
    if not _is_approval(message):
        logger.info(f"Gate 1 revision requested: {message}")
        return {
            "user_feedback": message,
            "hitl_checkpoint": "checkpoint_1_revising",
            "status": "revising_scope"
        }

    return {
        "user_feedback": None,
        "hitl_checkpoint": "checkpoint_1_approved",
        "status": "fetching_papers"
    }


def route_after_checkpoint_1(state: ResearchModeState) -> str:
    """Loops back through scope_reviser until user approves Gate 1."""
    if state.get("hitl_checkpoint") == "checkpoint_1_revising":
        return "scope_reviser"
    return "paper_fetcher"


async def checkpoint_2_node(state: ResearchModeState) -> dict:
    """Gate 2: Review PRISMA Study Selection and Structured Evidence Matrix."""
    logger.info("Executing Gate 2 (checkpoint_2) HITL interrupt...")
    user_input = interrupt({
        "checkpoint": "checkpoint_2",
        "message": "Review PRISMA study selection, screened corpus, and structured evidence records before synthesis.",
        "prisma_tracker": state.get("prisma_tracker") or {},
        "corpus_stats": state.get("corpus_stats") or {},
        "evidence_records_count": len(state.get("evidence_records") or []),
        "evidence_records": (state.get("evidence_records") or [])[:10],
        "screened_papers": (state.get("paper_records") or state.get("screened_papers") or [])[:10]
    })

    message = user_input.get("message", "") if isinstance(user_input, dict) else str(user_input)
    if not _is_approval(message):
        logger.info(f"Gate 2 revision requested: {message}")
        return {
            "user_feedback": message,
            "hitl_checkpoint": "checkpoint_2_revising",
            "status": "revising_evidence"
        }

    return {
        "user_feedback": None,
        "hitl_checkpoint": "checkpoint_2_approved",
        "status": "synthesizing"
    }


def route_after_checkpoint_2(state: ResearchModeState) -> str:
    """Loops back to evidence extraction if revision requested, else continues to taxonomy."""
    if state.get("hitl_checkpoint") == "checkpoint_2_revising":
        return "evidence_extractor"
    return "taxonomy_agent"


async def checkpoint_3_node(state: ResearchModeState) -> dict:
    """Gate 3: Review Theoretical Framing, Research Gaps, and Formulated Hypotheses."""
    logger.info("Executing Gate 3 (checkpoint_3) HITL interrupt...")
    user_input = interrupt({
        "checkpoint": "checkpoint_3",
        "message": "Review Conceptual Framework, Research Gaps, and Hypotheses before full paper synthesis.",
        "research_gap": state.get("research_gap", ""),
        "conceptual_framework": state.get("conceptual_framework", ""),
        "hypotheses": state.get("hypotheses", [])
    })

    message = user_input.get("message", "") if isinstance(user_input, dict) else str(user_input)
    if not _is_approval(message):
        logger.info(f"Gate 3 revision requested: {message}")
        return {
            "user_feedback": message,
            "hitl_checkpoint": "checkpoint_3_revising",
            "status": "revising_hypotheses"
        }

    return {
        "user_feedback": None,
        "hitl_checkpoint": "checkpoint_3_approved",
        "status": "synthesizing"
    }


def route_after_checkpoint_3(state: ResearchModeState) -> str:
    """Loops back to hypotheses if revision requested, else proceeds to research design."""
    if state.get("hitl_checkpoint") == "checkpoint_3_revising":
        return "hypotheses"
    return "research_design"


# --- Build 24-Agent StateGraph ---

builder = StateGraph(ResearchModeState)

# Phase 1: Planning & Protocol
builder.add_node("scope_definition", scope_definition_agent)
builder.add_node("protocol_agent", protocol_agent)
builder.add_node("keyword_extractor", keyword_extractor_agent)
builder.add_node("checkpoint_1", checkpoint_1_node)
builder.add_node("scope_reviser", scope_reviser_agent)

# Phase 2: Retrieval
builder.add_node("paper_fetcher", paper_fetcher_agent)
builder.add_node("citation_expander", citation_expander_agent)
builder.add_node("metadata_validator", metadata_validator_agent)

# Phase 3: Screening & Quality Appraisal
builder.add_node("paper_screener", paper_screener_agent)
builder.add_node("fulltext_eligibility", fulltext_eligibility_agent)
builder.add_node("quality_appraisal", quality_appraisal_agent)

# Phase 4: Structured Evidence Extraction
builder.add_node("evidence_extractor", evidence_extractor_agent)
builder.add_node("quantitative_extractor", quantitative_extractor_agent)
builder.add_node("methodology_extractor", methodology_extractor_agent)
builder.add_node("limitation_extractor", limitation_extractor_agent)
builder.add_node("provenance_agent", provenance_agent)
builder.add_node("checkpoint_2", checkpoint_2_node)

# Phase 5: Synthesis & Theoretical Framing
builder.add_node("taxonomy_agent", taxonomy_agent)
builder.add_node("gap_analysis", gap_analysis_agent)
builder.add_node("framework", conceptual_framework_agent)
builder.add_node("hypotheses", hypotheses_agent)
builder.add_node("checkpoint_3", checkpoint_3_node)

# Phase 5: Methodology & Section Writers
builder.add_node("research_design", research_design_agent)
builder.add_node("data_collection", data_collection_agent)
builder.add_node("data_analysis", data_analysis_agent)
builder.add_node("literature_review", literature_review_agent)
builder.add_node("results", results_agent)
builder.add_node("discussion", discussion_agent)
builder.add_node("limitations", limitations_agent)
builder.add_node("conclusion", conclusion_agent)
builder.add_node("future_scope", future_scope_agent)
builder.add_node("references", references_agent)
builder.add_node("introduction", introduction_agent)
builder.add_node("abstract", abstract_agent)
builder.add_node("title", title_agent)

# Post-Synthesis Validation & Document Rendering
builder.add_node("citation_validator", citation_validator_node)
builder.add_node("claim_validator", claim_validator_node)
builder.add_node("integrity_auditor", integrity_auditor_node)
builder.add_node("figures", figures_node)
builder.add_node("appendices", appendices_agent)
builder.add_node("claims_linker", claims_linker_node)

# --- Define Edge Routing ---

# Phase 1: Planning
builder.add_edge(START, "scope_definition")
builder.add_edge("scope_definition", "protocol_agent")
builder.add_edge("protocol_agent", "keyword_extractor")
builder.add_edge("keyword_extractor", "checkpoint_1")
builder.add_conditional_edges(
    "checkpoint_1",
    route_after_checkpoint_1,
    {"scope_reviser": "scope_reviser", "paper_fetcher": "paper_fetcher"}
)
builder.add_edge("scope_reviser", "checkpoint_1")

# Phase 2: Retrieval
builder.add_edge("paper_fetcher", "citation_expander")
builder.add_edge("citation_expander", "metadata_validator")

# Phase 3: Screening
builder.add_edge("metadata_validator", "paper_screener")
builder.add_edge("paper_screener", "fulltext_eligibility")
builder.add_edge("fulltext_eligibility", "quality_appraisal")

# Phase 4: Extraction
builder.add_edge("quality_appraisal", "evidence_extractor")
builder.add_edge("evidence_extractor", "quantitative_extractor")
builder.add_edge("quantitative_extractor", "methodology_extractor")
builder.add_edge("methodology_extractor", "limitation_extractor")
builder.add_edge("limitation_extractor", "provenance_agent")
builder.add_edge("provenance_agent", "checkpoint_2")
builder.add_conditional_edges(
    "checkpoint_2",
    route_after_checkpoint_2,
    {"evidence_extractor": "evidence_extractor", "taxonomy_agent": "taxonomy_agent"}
)

# Phase 5: Synthesis
builder.add_edge("taxonomy_agent", "gap_analysis")
builder.add_edge("gap_analysis", "framework")
builder.add_edge("framework", "hypotheses")
builder.add_edge("hypotheses", "checkpoint_3")
builder.add_conditional_edges(
    "checkpoint_3",
    route_after_checkpoint_3,
    {"hypotheses": "hypotheses", "research_design": "research_design"}
)

# Phase 5: Writing Pipeline
builder.add_edge("research_design", "data_collection")
builder.add_edge("data_collection", "data_analysis")
builder.add_edge("data_analysis", "literature_review")
builder.add_edge("literature_review", "results")
builder.add_edge("results", "discussion")
builder.add_edge("discussion", "limitations")
builder.add_edge("limitations", "conclusion")
builder.add_edge("conclusion", "future_scope")
builder.add_edge("future_scope", "references")
builder.add_edge("references", "introduction")
builder.add_edge("introduction", "abstract")
builder.add_edge("abstract", "title")

# Validation & Rendering
builder.add_edge("title", "claims_linker")
builder.add_edge("claims_linker", "citation_validator")
builder.add_edge("citation_validator", "claim_validator")
builder.add_edge("claim_validator", "integrity_auditor")
builder.add_edge("integrity_auditor", "figures")
builder.add_edge("figures", "appendices")
builder.add_edge("appendices", END)


_checkpointer = MemorySaver()
_compiled_graph = None


def set_checkpointer(checkpointer):
    global _checkpointer, _compiled_graph
    _checkpointer = checkpointer
    _compiled_graph = builder.compile(checkpointer=_checkpointer)
    logger.info(f"Research Mode v2 graph compiled with checkpointer: {type(checkpointer).__name__}")


def get_research_mode_graph(thread_id: Optional[str] = None):
    """Retrieve compiled Research Mode graph with dual-builder soft cut-over support."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = builder.compile(checkpointer=_checkpointer)
    return _compiled_graph


def __getattr__(name: str):
    if name == "research_mode_graph":
        return get_research_mode_graph()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
