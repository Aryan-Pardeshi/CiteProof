"""Legacy Research Mode Graph Builder (v1) for backward compatibility."""

import logging
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from backend.app.agents.research_mode.planning import (
    keyword_extractor_agent,
    scope_definition_agent,
    scope_reviser_agent,
)
from backend.app.agents.research_mode.retrieval import paper_fetcher_agent
from backend.app.agents.research_mode.screening import (
    fulltext_eligibility_agent,
    paper_screener_agent,
)
from backend.app.agents.research_mode.synthesis import (
    conceptual_framework_agent,
    gap_analysis_agent,
    hypotheses_agent,
)
from backend.app.agents.research_mode.validation import citation_validator_node
from backend.app.agents.research_mode.writing import (
    abstract_agent,
    appendices_agent,
    conclusion_agent,
    data_analysis_agent,
    data_collection_agent,
    discussion_agent,
    figures_node,
    future_scope_agent,
    introduction_agent,
    limitations_agent,
    literature_review_agent,
    references_agent,
    research_design_agent,
    results_agent,
    title_agent,
)
from backend.app.graph.research_mode_state import ResearchModeState

logger = logging.getLogger(__name__)
APPROVAL_WORDS = ["", "approve", "approved", "ok", "yes", "looks good", "continue"]

def _is_approval(message: str) -> bool:
    return (message or "").strip().lower() in APPROVAL_WORDS

async def checkpoint_1_node(state: ResearchModeState) -> dict:
    user_input = interrupt({
        "checkpoint": "checkpoint_1",
        "message": "Review Problem Statement, Objectives, Questions, and Keywords before paper fetching.",
        "keywords": state.get("keywords", []),
        "problem_statement": state.get("problem_statement", ""),
        "research_objectives": state.get("research_objectives", []),
        "research_questions": state.get("research_questions", [])
    })
    message = user_input.get("message", "") if isinstance(user_input, dict) else str(user_input)
    if not _is_approval(message):
        return {"user_feedback": message, "hitl_checkpoint": "checkpoint_1_revising", "status": "revising_scope"}
    return {"user_feedback": None, "hitl_checkpoint": "checkpoint_1_approved", "status": "fetching_papers"}

def route_after_checkpoint_1(state: ResearchModeState) -> str:
    if state.get("hitl_checkpoint") == "checkpoint_1_revising":
        return "scope_reviser"
    return "paper_fetcher"

async def hypotheses_node_v1(state: ResearchModeState) -> dict:
    """Wrapper for v1 ensuring no unexpected checkpoint_3 interrupt occurs."""
    res = await hypotheses_agent(state)
    if not isinstance(res, dict):
        logger.warning(
            f"hypotheses_agent returned {type(res).__name__}; using checkpoint-only state update."
        )
        return {"hitl_checkpoint": "checkpoint_1_approved", "status": "synthesizing"}
    res["hitl_checkpoint"] = "checkpoint_1_approved"
    res["status"] = "synthesizing"
    return res

builder_v1 = StateGraph(ResearchModeState)
builder_v1.add_node("scope_definition", scope_definition_agent)
builder_v1.add_node("keyword_extractor", keyword_extractor_agent)
builder_v1.add_node("checkpoint_1", checkpoint_1_node)
builder_v1.add_node("scope_reviser", scope_reviser_agent)
builder_v1.add_node("paper_fetcher", paper_fetcher_agent)
builder_v1.add_node("paper_screener", paper_screener_agent)
builder_v1.add_node("fulltext_fetcher", fulltext_eligibility_agent)
builder_v1.add_node("literature_review", literature_review_agent)
builder_v1.add_node("citation_verifier", citation_validator_node)
builder_v1.add_node("gap_analysis", gap_analysis_agent)
builder_v1.add_node("framework", conceptual_framework_agent)
builder_v1.add_node("hypotheses", hypotheses_node_v1)
builder_v1.add_node("research_design", research_design_agent)
builder_v1.add_node("data_collection", data_collection_agent)
builder_v1.add_node("data_analysis", data_analysis_agent)
builder_v1.add_node("results", results_agent)
builder_v1.add_node("discussion", discussion_agent)
builder_v1.add_node("limitations", limitations_agent)
builder_v1.add_node("conclusion", conclusion_agent)
builder_v1.add_node("future_scope", future_scope_agent)
builder_v1.add_node("references", references_agent)
builder_v1.add_node("figures", figures_node)
builder_v1.add_node("appendices", appendices_agent)
builder_v1.add_node("introduction", introduction_agent)
builder_v1.add_node("abstract", abstract_agent)
builder_v1.add_node("title", title_agent)

builder_v1.add_edge(START, "scope_definition")
builder_v1.add_edge("scope_definition", "keyword_extractor")
builder_v1.add_edge("keyword_extractor", "checkpoint_1")
builder_v1.add_conditional_edges("checkpoint_1", route_after_checkpoint_1, {"scope_reviser": "scope_reviser", "paper_fetcher": "paper_fetcher"})
builder_v1.add_edge("scope_reviser", "checkpoint_1")
builder_v1.add_edge("paper_fetcher", "paper_screener")
builder_v1.add_edge("paper_screener", "fulltext_fetcher")
builder_v1.add_edge("fulltext_fetcher", "literature_review")
builder_v1.add_edge("literature_review", "citation_verifier")
builder_v1.add_edge("citation_verifier", "gap_analysis")
builder_v1.add_edge("gap_analysis", "framework")
builder_v1.add_edge("framework", "hypotheses")
builder_v1.add_edge("hypotheses", "research_design")
builder_v1.add_edge("research_design", "data_collection")
builder_v1.add_edge("data_collection", "data_analysis")
builder_v1.add_edge("data_analysis", "results")
builder_v1.add_edge("results", "discussion")
builder_v1.add_edge("discussion", "limitations")
builder_v1.add_edge("limitations", "conclusion")
builder_v1.add_edge("conclusion", "future_scope")
builder_v1.add_edge("future_scope", "references")
builder_v1.add_edge("references", "figures")
builder_v1.add_edge("figures", "appendices")
builder_v1.add_edge("appendices", "introduction")
builder_v1.add_edge("introduction", "abstract")
builder_v1.add_edge("abstract", "title")
builder_v1.add_edge("title", END)

_checkpointer_v1 = MemorySaver()
_compiled_graph_v1 = None
_last_checkpointer_v1 = None

def get_research_mode_graph_v1(checkpointer=None):
    global _compiled_graph_v1, _last_checkpointer_v1
    cp = checkpointer or _checkpointer_v1
    if _compiled_graph_v1 is None or _last_checkpointer_v1 != cp:
        _compiled_graph_v1 = builder_v1.compile(checkpointer=cp)
        _last_checkpointer_v1 = cp
    return _compiled_graph_v1
