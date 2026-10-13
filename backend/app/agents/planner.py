# Planner agent
import sys
from pathlib import Path

# Add workspace root to sys.path so 'backend' is importable
root_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import logging

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from backend.app.graph.state import ResearchState
from backend.app.llm import invoke_structured, llm

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a strategic research planner.

Given a research query, create a structured research plan.

Output:
1. Problem Statement (ps)
- Explain the core research gap, uncertainty, limitation, or challenge.
- Describe the difference between current understanding and desired understanding.
- Do not propose solutions.
- Keep it specific and research-focused.
- Length: 1 to 3 sentences.
- Do not use em dashes.

2. Sub-tasks (sub_tasks)
Generate at most 5 independent research areas.

Rules:
- Each sub-task must investigate one distinct aspect of the query.
- Each sub-task must be understandable without reading other tasks.
- Each sub-task must represent a concrete research objective.
- Keep each sub-task under 20 words.
- Avoid vague tasks.
- Do not create meta-tasks like:
  "summarize findings"
  "compile information"
  "review literature"
  "analyze all research"

Example:

Query:
"room temperature superconductivity 2026"

Good ps:
"Despite progress in superconductivity research, achieving superconductivity at practical temperatures and pressures remains unresolved. This research examines current evidence, competing theories, and barriers preventing reliable room-temperature superconductors."

Good sub_tasks:
[
"Investigate LK-99 replication studies and experimental outcomes from 2024-2026",
"Examine hydrogen-rich compounds as high-temperature superconductor candidates",
"Analyze theoretical mechanisms proposed for room-temperature superconductivity",
"Evaluate experimental challenges preventing practical superconducting materials",
"Study recent superconductivity measurement and verification methods"
]

Revision rules:
- If the user requests a ps change, modify only ps.
- If the user requests sub-task changes, modify only sub_tasks.
- Preserve unchanged sections exactly when not requested.
- Do not rewrite the entire plan unnecessarily.

Return ONLY valid JSON:
{{
  "ps": "string",
  "sub_tasks": ["string"]
}}
"""


class ResearchPlan(BaseModel):
    ps: str | None = Field(
        default=None,
        description="Detailed Problem Statement of the research plan. Omit if not revising.",
    )
    sub_tasks: list[str] | None = Field(
        default=None,
        description="Ordered list of independent research sub-tasks. Omit if not revising.",
        min_length=1,
        max_length=5,
    )


def planner_node(state: ResearchState) -> dict:

    user_content = (
        f"Create a Problem statement (ps) and a research plan for: {state['query']}"
    )
    if state.get("user_feedback"):
        user_content += f"\n\nUser feedback on previous ps and plan : {state['user_feedback']}\nPrevious ps: {state.get('ps', '')}\nPrevious plan: {state.get('plan', [])}"

    prompt = ChatPromptTemplate([("system", SYSTEM_PROMPT), ("user", user_content)])
    messages = prompt.format_messages()

    try:
        result = invoke_structured(llm, ResearchPlan, messages)
        new_ps = result.ps if result.ps is not None else state.get("ps", "")
        new_plan = (
            result.sub_tasks if result.sub_tasks is not None else state.get("plan", [])
        )
        logger.info(
            f"Planner generated {len(new_plan)} sub-tasks for query: {state['query']}"
        )
        return {"ps": new_ps, "plan": new_plan, "status": "awaiting_approval"}
    except Exception as e:
        error_msg = str(e)
        logger.exception("Error generating research plan")

        error_lower = error_msg.lower()
        if any(
            kw in error_lower
            for kw in ["violates safety", "safety", "inappropriate", "restricted"]
        ):
            friendly_error = "Query contains inappropriate or restricted content."
        elif any(
            kw in error_lower
            for kw in [
                "api key",
                "authentication",
                "unauthorized",
                "401",
                "403",
                "invalid key",
                "missing credentials",
                "not set",
            ]
        ):
            friendly_error = "API key is missing or invalid. Open settings to configure your API keys."
        else:
            friendly_error = f"Failed to generate research plan: {error_msg}"

        return {"status": "error", "error": friendly_error}


if __name__ == "__main__":
    test_state = {
        "query": "Recent advances in room-temperature superconductivity in 2026",
        "plan": [],
        "plan_approved": False,
        "user_feedback": None,
        "status": "planning",
        "results": [],
        "final_answer": None,
        "citations": [],
    }
    print("Testing planner_node...")
    output = planner_node(test_state)
    print("Output:", output)
