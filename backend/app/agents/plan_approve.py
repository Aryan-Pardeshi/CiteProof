#Plan Approval Agent

import sys
from pathlib import Path

# Add workspace root to sys.path so 'backend' is importable
root_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import logging

from langchain_core.prompts import ChatPromptTemplate
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from backend.app.graph.state import ResearchState
from backend.app.llm import invoke_structured, llm_fast

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = ("""You are a plan approval classifier.
Analyze the user's feedback to determine if they approved the current plan, or if they are requesting revisions or changes.

Classification Rules:
- If the user explicitly approves the plan (e.g., "looks good", "approve", "proceed", "go ahead", "run", "yes", "perfect", "ok"), set 'plan_approved' to true.
- If the user requests any change, addition, deletion, modification, or feedback (e.g., "add trend analysis step", "remove step 2", "change X", "focus on Y"), set 'plan_approved' to false.
- Be conservative: if they ask questions, provide suggestions, or demand adjustments, set 'plan_approved' to false.
- Respond in JSON format matching the schema.""")

class PlanState(BaseModel):
    plan_approved: bool = Field(description="Whether the plan is approved or not")

def plan_approval(state: ResearchState)->dict:
    # Pause execution to wait for user approval/feedback message
    user_response = interrupt("Waiting for plan approval/feedback")
    
    # Extract the message from the resume payload (could be dict or string)
    feedback = user_response.get("message", "") if isinstance(user_response, dict) else str(user_response)
    
    
    user_content = f"User feedback: {feedback}\nPrevious ps: {state.get('ps', '')}\nPrevious plan: {state.get('plan', [])}"

    prompt = ChatPromptTemplate([("system", SYSTEM_PROMPT), ("user", user_content)])
    messages = prompt.format_messages()
    result = invoke_structured(llm_fast, PlanState, messages)
    
    logger.info(f"Plan approval result: {result.plan_approved}")
    logger.info(f"User feedback: {feedback}")
    
    status = "researching" if result.plan_approved else "planning"
    return {"plan_approved": result.plan_approved, "user_feedback": feedback, "status": status}