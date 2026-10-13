import operator
from typing import Annotated, List, Literal, Optional, TypedDict


class ResearchState(TypedDict):
    """State for the research assistant graph."""

    query: str 
    ps: str
    plan: List[str]
    plan_approved: bool
    user_feedback: Optional[str]
    status: Literal["planning", "awaiting_approval", "researching", "reviewing", "completed", "error"]
    #append the previous value form the previous state type do not overwrite existing values
    results: Annotated[List[str], operator.add] 
    final_answer: Optional[str]
    citations: Annotated[List[str], operator.add]
    error: Optional[str]
    search_topic: List[Literal["all", "news", "academic", "finance", "patent"]]
