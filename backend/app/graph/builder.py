import sys
from pathlib import Path

# Add workspace root to sys.path so 'backend' is importable
root_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

# LangGraph graph definition
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from backend.app.agents.aggregator import aggregator_node
from backend.app.agents.plan_approve import plan_approval
from backend.app.agents.planner import planner_node
from backend.app.agents.query_validator import query_validator
from backend.app.agents.researcher import researcher_node
from backend.app.agents.supervisor import dispatch_researchers
from backend.app.graph.state import ResearchState

# state = memory


builder = StateGraph(ResearchState)

# nodes
builder.add_node("query_validator", query_validator)
builder.add_node("planner", planner_node)
builder.add_node("approval", plan_approval)
builder.add_node("researcher", researcher_node)
builder.add_node("aggregator", aggregator_node)


def route_after_approval(state: ResearchState):
    """Fan out to one researcher per sub-task if approved, otherwise loop back to planner."""
    if state.get("plan_approved"): #if plan approved true
        return dispatch_researchers(state)  # returns List[Send("researcher", ...)]
    return "planner"


# Connect nodes
builder.add_edge(START, "query_validator")
# builder.add_edge("query_validator", "planner")
builder.add_conditional_edges(
    "query_validator", lambda state: END if state.get("error") else "planner"  , ["planner", END])
builder.add_conditional_edges(
    "planner", lambda state: END if state.get("error") else "approval", ["approval", END])
# conditional node because if approved it goes to aggregator and if not approved it goes back to planner
# also Langchain dosnt allow list[Send()] from normal nodes only allows state to be returned
builder.add_conditional_edges(
    "approval", route_after_approval, ["researcher", "planner"]) #the 3rd parameter gives strict runtime whitelist

#we dont have approval , researcher edge coz we are using Send() 
builder.add_edge("researcher", "aggregator")
builder.add_edge("aggregator", END)


_checkpointer = MemorySaver()
_compiled_graph = None


def set_checkpointer(checkpointer):
    global _checkpointer, _compiled_graph
    _checkpointer = checkpointer
    _compiled_graph = builder.compile(checkpointer=_checkpointer)


def get_research_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = builder.compile(checkpointer=_checkpointer)
    return _compiled_graph


def __getattr__(name: str):
    if name == "research_graph":
        return get_research_graph()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

#############################


if __name__ == "__main__":
    import sys

    # Force UTF-8 output on Windows consoles
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    config = {"configurable": {"thread_id": "test_thread_revision_1"}}

    print("--- Running graph (Starting Planner) ---")
    research_graph = get_research_graph()
    for event in research_graph.stream(
        {"query": "Recent breakthroughs in Quantum Computing in 2026"}, config=config
    ):
        print("Event:", event)

    # 1. Inspect the state after first interrupt
    state = research_graph.get_state(config)
    print("\n--- Paused State (Initial Plan) ---")
    print("Next step:", state.next)
    print("Current Plan:", state.values.get("plan"))

    # 2. Resume the graph by passing feedback rejecting the plan
    print("\n--- Resuming from Interrupt (Submitting feedback: 'no 3rd point is wrong') ---")
    for event in research_graph.stream(
        Command(resume={"message": "no 3rd point is wrong"}), config=config
    ):
        print("Event:", event)

    # 3. Inspect the state after the second interrupt (should show revised plan)
    revised_state = research_graph.get_state(config)
    print("\n--- Paused State (Revised Plan) ---")
    print("Next step:", revised_state.next)
    print("Current Plan:", revised_state.values.get("plan"))
    print("User Feedback stored:", revised_state.values.get("user_feedback"))

    # 4. Resume the graph by approving the plan
    print("\n--- Resuming from Interrupt (Approving: 'yeah looks good') ---")
    for event in research_graph.stream(
        Command(resume={"message": "yeah looks good"}), config=config
    ):
        print("Event:", event)

    # 5. Check final state
    final_state = research_graph.get_state(config)
    print("\n--- Completed State ---")
    print("Next step:", final_state.next)
    print("Plan Approved:", final_state.values.get("plan_approved"))
    print("Final Plan:", final_state.values.get("plan"))


