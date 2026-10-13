from typing import Any, Dict, List, Optional, TypedDict


class ResearchModeState(TypedDict, total=False):
    """State schema for Autonomous Research Mode pipeline.
    
    Contains typed evidence store collections alongside legacy fields for
    backward compatibility during document compilation and UI updates.
    """
    thread_id: str
    mode: str  # "research"
    problem_statement: str
    research_objectives: List[str]
    research_questions: List[str]
    keywords: List[str]
    
    # --- Structured Evidence Store (Source of Truth) ---
    paper_records: List[Dict[str, Any]]       # Serialized PaperRecord objects
    evidence_records: List[Dict[str, Any]]    # Serialized EvidenceRecord objects
    evidence_spans: List[Dict[str, Any]]      # Serialized EvidenceSpan objects (verbatim anchors)
    claims: List[Dict[str, Any]]              # Serialized Claim objects with resolved traceability chains
    review_claims: List[Dict[str, Any]]       # Serialized ReviewClaim objects
    prisma_tracker: Dict[str, Any]            # Serialized PRISMATracker
    search_protocol: Dict[str, Any]           # Serialized SearchProtocol
    taxonomy: Dict[str, Any]                  # Serialized Taxonomy themes
    validation_report: Dict[str, Any]         # Serialized ValidationReport
    research_gaps_structured: List[Dict[str, Any]]
    synthesis_comparisons: List[Dict[str, Any]]

    # --- Legacy Raw Dictionaries (retained for backward compatibility) ---
    raw_papers: List[Dict[str, Any]]
    screened_papers: List[Dict[str, Any]]
    unverified_citations: List[str]

    # --- Paper Prose Sections (Rendered from Evidence Store) ---
    literature_review: str
    research_gap: str
    conceptual_framework: str
    hypotheses: List[str]
    research_design: str
    data_collection_plan: str
    data_analysis_plan: str
    results: str
    discussion: str
    implications: str
    limitations: str
    conclusion: str
    future_scope: List[str] | str
    references: List[str]  # Formatted APA citations
    appendices: str
    introduction: str
    abstract: str
    title: str

    # --- LangGraph & HITL Execution Control ---
    hitl_checkpoint: str  # "checkpoint_1", "checkpoint_1_revising", "checkpoint_1_approved", "checkpoint_2", "checkpoint_2_revising", "checkpoint_2_approved", "checkpoint_3", "checkpoint_3_revising", "checkpoint_3_approved", "completed"
    user_feedback: Optional[str]
    status: str  # "initializing", "awaiting_approval", "fetching_papers", "synthesizing", "completed", "error"
    error: Optional[str]

    corpus_stats: Dict[str, int]
    figures: Dict[str, str]     # figure name -> absolute image path
    model_overrides: Dict[str, str]     # role -> model name
