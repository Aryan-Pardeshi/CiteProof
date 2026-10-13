import operator
from typing import Annotated, Literal, TypedDict


class ResearchState(TypedDict):
    """State for the research assistant graph."""

    query: str 
    ps: str
    plan: list[str]
    plan_approved: bool
    user_feedback: str | None
    status: Literal["planning", "awaiting_approval", "researching", "reviewing", "completed", "error"]
    #append the previous value form the previous state type do not overwrite existing values
    results: Annotated[list[str], operator.add]
    final_answer: str | None
    citations: Annotated[list[str], operator.add]
    error: str | None
    search_topic: list[Literal["all", "news", "academic", "finance", "patent"]]
