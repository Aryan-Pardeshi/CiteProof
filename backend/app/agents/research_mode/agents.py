"""Backward-compatibility module redirecting to modular agent packages."""

from backend.app.agents.research_mode import *
from backend.app.agents.research_mode.validation import validate_citations_in_text

# Alias for legacy test suites
verify_citations = validate_citations_in_text
