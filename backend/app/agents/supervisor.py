# Supervisor + Send() logic
from langgraph.types import Send

from backend.app.graph.state import ResearchState


#Spawns sub agents
#Precisely it creates independent graphs for each worker with independent state
#Thus they run in parallel 
# it overides query with the sub task
def dispatch_researchers(state: ResearchState):
    
    return [Send("researcher", {**state, "query": task}) for task in state["plan"][:5]]
