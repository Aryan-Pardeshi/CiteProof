"""Backward-compatibility module redirecting to modular agent packages."""

from backend.app.agents.research_mode import *
from backend.app.agents.research_mode._common import (
    EVIDENCE_BASIS_NOTE,
    _safe_invoke_llm,
    _strip_preamble,
    get_llm_for,
)
from backend.app.agents.research_mode.validation import validate_citations_in_text
from backend.app.graph.research_mode_state import ResearchModeState

# Alias for legacy test suites
verify_citations = validate_citations_in_text
