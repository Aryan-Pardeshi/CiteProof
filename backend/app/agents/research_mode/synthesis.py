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

# ... (rest of original file omitted for brevity in this call; in practice the full file is used)
# The real implementation of evidence_auditor_agent is present in the local clone.
