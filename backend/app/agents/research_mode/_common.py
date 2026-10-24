"""Common utilities and LLM invocation helpers for Research Mode agents."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from typing import Any

from langchain_core.messages import HumanMessage

from backend.app.llm import get_llm, invoke_llm
from backend.app.report_guard import ReportGenerationError

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


def strip_abstract_heading(text: str) -> str:
    """Strip a leading '# ' or '## ' (or any level) Abstract heading line if present."""
    if not text:
        return ""
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines:
        m = re.match(r"^#{1,6}\s+(.*)$", lines[0].strip())
        if m and m.group(1).rstrip(".:").strip().lower() == "abstract":
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)
    return "\n".join(lines).strip()


def normalize_section_body(text: str, *, section_title: str) -> str:
    """Normalize a section body returned by an agent.

    - Removes leading blank lines.
    - If the first non-blank line is a Markdown heading (# to ######), removes it
      (the exporter adds the section heading).
    - Removes an "Abstract" block that appears at the start of a non-abstract section:
      a heading whose text is "Abstract" (any level, case-insensitive), plus
      everything until the next heading.
    - Demotes every remaining heading so that the shallowest heading in the body
      becomes '###' (keeping relative depth; capped at '######'). Section bodies
      must never contain '#' or '##' headings.
    - Leaves all other text, [EV:...] markers, tables and lists untouched. Never drops citations.
    - Pure function, no I/O.
    """
    if not text:
        return ""

    lines = text.splitlines()

    # 1. Remove leading blank lines
    while lines and not lines[0].strip():
        lines.pop(0)

    if not lines:
        return ""

    is_non_abstract = bool(section_title and section_title.strip().lower() != "abstract")

    def _parse_heading(line: str) -> tuple[int, str] | None:
        m = re.match(r"^(\s*)(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(2))
            title = m.group(3).strip()
            return level, title
        return None

    def _is_abstract_heading(heading_text: str) -> bool:
        clean = heading_text.rstrip(".:").strip().lower()
        return clean == "abstract"

    def _is_section_or_doc_title(level: int, title: str) -> bool:
        # Any level 1 heading (# ) is a document/section title that must never appear in a section body
        if level == 1:
            return True
        t_clean = title.rstrip(".:").strip().lower()
        s_clean = section_title.rstrip(".:").strip().lower() if section_title else ""
        if t_clean in ("title", "document title", "paper title", "section title", "research report"):
            return True
        if s_clean and (t_clean == s_clean or s_clean in t_clean):
            return True
        # If it doesn't match section title, check if it's a numbered subsection (e.g. "1. Scope", "1.1 Details")
        # Subsections start with a number (like "1.", "1.1") and should not be removed as section titles
        if re.match(r"^\d+(\.\d+)*[.)]?\s+", title.strip()):
            return False
        # Any non-numbered heading on the first non-blank line is treated as the section title
        return True

    # 2. If the first non-blank line is a Markdown heading (# to ######), remove it
    first_h = _parse_heading(lines[0])
    if first_h:
        level, title = first_h
        if is_non_abstract and _is_abstract_heading(title):
            # Abstract block at the start of a non-abstract section: remove heading and all lines until next heading
            lines.pop(0)
            while lines:
                if _parse_heading(lines[0]) is not None:
                    break
                lines.pop(0)
        elif _is_section_or_doc_title(level, title):
            lines.pop(0)

    # Remove blank lines after removing the first heading
    while lines and not lines[0].strip():
        lines.pop(0)

    # 3. Remove an "Abstract" block that appears at the start of a non-abstract section
    if lines and is_non_abstract:
        h = _parse_heading(lines[0])
        if h and _is_abstract_heading(h[1]):
            lines.pop(0)
            while lines:
                if _parse_heading(lines[0]) is not None:
                    break
                lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)

    if not lines:
        return ""

    # 4. Demote every remaining heading so that the shallowest heading in the body becomes ###
    in_code_block = False
    heading_levels: list[int] = []
    parsed_lines: list[tuple[bool, int | None, str, str]] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_block = not in_code_block
            parsed_lines.append((False, None, "", line))
            continue

        if in_code_block:
            parsed_lines.append((False, None, "", line))
            continue

        m = re.match(r"^(\s*)(#{1,6})\s+(.*)$", line)
        if m:
            indent = m.group(1)
            level = len(m.group(2))
            rest = m.group(3)
            heading_levels.append(level)
            parsed_lines.append((True, level, indent, rest))
        else:
            parsed_lines.append((False, None, "", line))

    if not heading_levels:
        return "\n".join(lines).strip()

    min_level = min(heading_levels)
    shift = max(0, 3 - min_level)

    result_lines: list[str] = []
    for is_heading, level, indent, content in parsed_lines:
        if is_heading and level is not None:
            new_level = min(6, level + shift)
            new_hashes = "#" * new_level
            result_lines.append(f"{indent}{new_hashes} {content}")
        else:
            result_lines.append(content)

    return "\n".join(result_lines).strip()


async def _safe_invoke_llm(
    llm,
    messages: list[Any],
    max_retries: int = 4,
    base_backoff: float = 2.0,
    node: str = "llm",
) -> str:
    """Async invoke LLM with non-blocking exponential backoff on transient errors.

    Only the exception type is logged: provider messages can echo request bodies
    and must never leak prompts, keys, or user data into logs. ``node`` names the
    caller so a retry storm is attributable in production logs.
    """
    last_err_type = None
    for attempt in range(max_retries + 1):
        try:
            res = await asyncio.to_thread(invoke_llm, llm, messages)
            content = res.content if hasattr(res, "content") else str(res)
            return _strip_preamble(str(content))
        except ReportGenerationError:
            raise
        except Exception as e:  # noqa: BLE001
            last_err_type = type(e).__name__
            logger.warning(
                "[%s] LLM invocation attempt %d/%d failed (%s).",
                node,
                attempt + 1,
                max_retries + 1,
                last_err_type,
            )
            if attempt < max_retries:
                await asyncio.sleep(min(base_backoff ** attempt, 10.0))

    logger.error(
        "[%s] LLM invocation failed after %d attempts (%s).",
        node,
        max_retries + 1,
        last_err_type or "unknown",
    )
    return ""


def raise_required_section_failure(*, section: str, node: str = "llm") -> None:
    """Fail the run when a required paper section cannot be generated."""
    logger.error(
        "[%s] Required section %r is still empty after retries; failing the run.",
        node,
        section,
    )
    raise ReportGenerationError(f"required section {section!r} is empty after retries")


async def write_required_section(
    invoke: Callable[..., Any],
    llm,
    prompt: str,
    *,
    section: str,
    compact_prompt: str | None = None,
    normalizer: Callable[[str], str] | None = None,
) -> str:
    """Write a required section, retrying once with reduced input, then fail.

    ``invoke`` is the caller module's ``_safe_invoke_llm`` so tests that patch
    that symbol keep working. The second attempt uses ``compact_prompt`` when
    supplied; both attempts run through the normaliser so headings and abstract
    leakage are removed before the emptiness check.
    """
    normalize = normalizer or (
        lambda text: normalize_section_body(text, section_title=section)
    )
    attempts = [prompt]
    if compact_prompt and compact_prompt != prompt:
        attempts.append(compact_prompt)

    for index, current_prompt in enumerate(attempts, start=1):
        raw = await invoke(llm, [HumanMessage(content=current_prompt)], node=section)
        body = normalize(_strip_preamble(raw))
        if body and body.strip():
            return body
        logger.warning(
            "[%s] Required section %r returned empty content (attempt %d/%d).",
            section,
            section,
            index,
            len(attempts),
        )

    raise_required_section_failure(section=section, node=section)
    return ""  # pragma: no cover - raise_required_section_failure always raises
