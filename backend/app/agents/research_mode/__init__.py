"""Research Mode Agents package."""

from backend.app.agents.research_mode.extraction import (
    evidence_extractor_agent,
    limitation_extractor_agent,
    methodology_extractor_agent,
    provenance_agent,
    quantitative_extractor_agent,
)
from backend.app.agents.research_mode.planning import (
    keyword_extractor_agent,
    protocol_agent,
    scope_definition_agent,
    scope_reviser_agent,
)
from backend.app.agents.research_mode.retrieval import (
    citation_expander_agent,
    metadata_validator_agent,
    paper_fetcher_agent,
)
from backend.app.agents.research_mode.screening import (
    fulltext_eligibility_agent,
    paper_screener_agent,
    quality_appraisal_agent,
)
from backend.app.agents.research_mode.synthesis import (
    conceptual_framework_agent,
    gap_analysis_agent,
    hypotheses_agent,
    taxonomy_agent,
)
from backend.app.agents.research_mode.validation import (
    citation_validator_node,
    claim_validator_node,
    integrity_auditor_node,
)
from backend.app.agents.research_mode.writing import (
    abstract_agent,
    appendices_agent,
    claims_linker_node,
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

__all__ = [  # noqa: RUF022 - exports remain grouped by pipeline phase.
    # Planning
    "scope_definition_agent",
    "protocol_agent",
    "keyword_extractor_agent",
    "scope_reviser_agent",
    # Retrieval
    "paper_fetcher_agent",
    "citation_expander_agent",
    "metadata_validator_agent",
    # Screening
    "paper_screener_agent",
    "fulltext_eligibility_agent",
    "quality_appraisal_agent",
    # Extraction
    "evidence_extractor_agent",
    "quantitative_extractor_agent",
    "methodology_extractor_agent",
    "limitation_extractor_agent",
    "provenance_agent",
    # Synthesis
    "taxonomy_agent",
    "gap_analysis_agent",
    "conceptual_framework_agent",
    "hypotheses_agent",
    # Writing
    "literature_review_agent",
    "research_design_agent",
    "data_collection_agent",
    "data_analysis_agent",
    "results_agent",
    "discussion_agent",
    "limitations_agent",
    "conclusion_agent",
    "future_scope_agent",
    "references_agent",
    "appendices_agent",
    "introduction_agent",
    "abstract_agent",
    "title_agent",
    "figures_node",
    "claims_linker_node",
    # Validation
    "citation_validator_node",
    "claim_validator_node",
    "integrity_auditor_node",
]
