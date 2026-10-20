"""Common utilities and LLM invocation helpers for Research Mode agents."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from backend.app.llm import get_llm

logger = logging.getLogger(__name__)

# System note appended to writing prompts enforcing evidence grounding
EVIDENCE_BASIS_NOTE = (
    "\n\nIMPORTANT RESEARCH INTEGRITY INSTRUCTION:\n"
    "- Synthesize findings strictly from the provided structured evidence base and cited literature.\n"
    "- If describing proposed methodology, research designs, or data collection instruments, "
    "explicitly frame them as PROPOSED future studies (using 'would be', 'is proposed to', 'future work should').\n"
    "- NEVER state or imply that the authors of this report executed original laboratory experiments, "
    "conducted human surveys, or obtained unpublished empirical measurements unless explicitly provided in the evidence base."
)


def get_llm_for(role: str, state: dict[str, Any], temperature: float = 0.0):
    """Retrieve an LLM instance with optional user model override from state."""
    model_overrides = state.get("model_overrides") or {}
    model = model_overrides.get(role)
    return get_llm(model=model, role=role, temperature=temperature)


def _strip_preamble(text: str) -> str:
    """Strip common LLM meta-chatter and markdown preamble fences."""
    if not text:
        return ""
    cleaned = re.sub(
        r"^(Here (is|are)|Below is|Sure,|Certainly,|As an AI research|In this section)[^\n]*\n+",
        "",
        text.strip(),
        flags=re.IGNORECASE
    )
    return cleaned.strip()


async def _safe_invoke_llm(
    llm,
    messages: list[Any],
    max_retries: int = 4,
    base_backoff: float = 2.0
) -> str:
    """Async invoke LLM with non-blocking exponential backoff on transient errors."""
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            res = await asyncio.to_thread(llm.invoke, messages)
            content = res.content if hasattr(res, "content") else str(res)
            return _strip_preamble(str(content))
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning(f"LLM invocation attempt {attempt+1}/{max_retries+1} failed: {e}")
            if attempt < max_retries:
                await asyncio.sleep(min(base_backoff ** attempt, 10.0))

    logger.error(f"LLM invocation failed after {max_retries+1} attempts: {last_err}")
    return ""
