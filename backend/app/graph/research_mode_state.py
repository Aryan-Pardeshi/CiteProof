from typing import Any, TypedDict


class ResearchModeState(TypedDict, total=False):
    """State schema for Autonomous Research Mode pipeline.
    
    Contains typed evidence store collections alongside legacy fields for
    backward compatibility during document compilation and UI updates.
    """
    thread_id: str
    mode: str  # "research"
    problem_statement: str
    research_objectives: list[str]
    research_questions: list[str]
    keywords: list[str]
    
    # --- Structured Evidence Store (Source of Truth) ---
    paper_records: list[dict[str, Any]]       # Serialized PaperRecord objects
    evidence_records: list[dict[str, Any]]    # Serialized EvidenceRecord objects
    evidence_spans: list[dict[str, Any]]      # Serialized EvidenceSpan objects (verbatim anchors)
    claims: list[dict[str, Any]]              # Serialized Claim objects with resolved traceability chains
    review_claims: list[dict[str, Any]]       # Serialized ReviewClaim objects
    prisma_tracker: dict[str, Any]            # Serialized PRISMATracker
    search_protocol: dict[str, Any]           # Serialized SearchProtocol
    taxonomy: dict[str, Any]                  # Serialized Taxonomy themes
    validation_report: dict[str, Any]         # Serialized ValidationReport
    research_gaps_structured: list[dict[str, Any]]
    synthesis_comparisons: list[dict[str, Any]]

    # --- Legacy Raw Dictionaries (retained for backward compatibility) ---
    raw_papers: list[dict[str, Any]]
    screened_papers: list[dict[str, Any]]
    unverified_citations: list[str]

    # --- Paper Prose Sections (Rendered from Evidence Store) ---
    literature_review: str
    research_gap: str
    conceptual_framework: str
    hypotheses: list[str]
    research_design: str
    data_collection_plan: str
    data_analysis_plan: str
    results: str
    discussion: str
    implications: str
    limitations: str
    conclusion: str
    future_scope: list[str] | str
    references: list[str]  # Formatted APA citations
    appendices: str
    introduction: str
    abstract: str
    title: str

    # --- LangGraph & HITL Execution Control ---
    hitl_checkpoint: str  # "checkpoint_1", "checkpoint_1_revising", "checkpoint_1_approved", "checkpoint_2", "checkpoint_2_revising", "checkpoint_2_approved", "checkpoint_3", "checkpoint_3_revising", "checkpoint_3_approved", "completed"
    user_feedback: str | None
    status: str  # "initializing", "awaiting_approval", "fetching_papers", "synthesizing", "completed", "error"
    error: str | None

    corpus_stats: dict[str, int]
    figures: dict[str, str]     # figure name -> absolute image path
    model_overrides: dict[str, str]     # role -> model name
