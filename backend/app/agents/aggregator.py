# Final report builder
import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from backend.app.graph.state import ResearchState
from backend.app.llm import ainvoke_llm, llm_pro

logger = logging.getLogger(__name__)


async def aggregator_node(state: ResearchState) -> dict:
    # Combine the results of all the sub-agents
    combined = "\n\n".join(
        f"Research Section {i+1}:\n{result}" 
        for i, result in enumerate(state["results"])
    )

    # Combine and deduplicate the citations
    citations = list(dict.fromkeys(state.get("citations", [])))

    messages = [
        SystemMessage(content=(
            "You are a Principal Research Analyst and Writer.\n\n"
            "Your task is to synthesize the provided raw research findings into a cohesive, publication-ready research report in markdown format.\n\n"
            "Structure requirements:\n"
            "- Use a single, clean markdown title for the report.\n"
            "- Provide a clear Executive Summary / Overview at the beginning.\n"
            "- Organically group the findings into logical sections based on the research content.\n"
            "- Incorporate the Problem Statement naturally into the intro/executive summary section.\n"
            "- List all the provided source URLs cleanly under a 'Sources & References' section at the end.\n\n"
            "Matplotlib Chart Tool instructions:\n"
            "- You have access to a matplotlib chart generation tool (`generate_matplotlib_chart`).\n"
            "- Analyze the research sections. If there are numerical data, comparisons, statistics, or historical trends, you MUST call this tool to generate charts.\n"
            "- You are REQUIRED to generate a minimum of 1 chart, and a maximum of 3 charts (e.g., bar chart, line plot, pie chart, scatter plot).\n"
            "- Embed the markdown image links returned by the tool directly inside the corresponding sections of your report.\n\n"
            "Guidelines:\n"
            "- Keep the tone formal, objective, and analytical.\n"
            "- Present data in well-formatted markdown tables or bullet points where appropriate.\n"
            "- Strictly stick to the facts provided. Do not extrapolate, invent metrics, or fabricate URLs."
        )),

        HumanMessage(content=(
            f"User Query: {state['query']}\n"
            f"Problem Statement (ps): {state.get('ps', '')}\n\n"
            f"Research Sections:\n{combined}\n\n"
            f"Citations:\n" + "\n".join(f"- {url}" for url in citations) + "\n\n"
            "Write the final synthesized markdown report. Remember to call `generate_matplotlib_chart` to generate between 1 and 3 charts based on the research findings data."
        ))
    ]

    try:
        from langchain_core.messages import ToolMessage

        from backend.app.tools.matplotlib_tool import generate_matplotlib_chart
        
        llm_with_tools = llm_pro.bind_tools([generate_matplotlib_chart])
        
        messages_history = list(messages)
        
        while True:
            response = await ainvoke_llm(llm_with_tools, messages_history)
            messages_history.append(response)
            
            if response.tool_calls:
                for tool_call in response.tool_calls:
                    if tool_call["name"] == "generate_matplotlib_chart":
                        # Execute the tool
                        result_md_image = generate_matplotlib_chart.invoke(tool_call["args"])
                        
                        tool_message = ToolMessage(
                            content=result_md_image,
                            tool_call_id=tool_call["id"],
                            name=tool_call["name"]
                        )
                        messages_history.append(tool_message)
                continue
            else:
                break
                
        # Reconstruct the entire final report from all assistant turns (since intermediate turns have tool calls and partial text)
        final_content = "".join(
            msg.content for msg in messages_history if isinstance(msg, AIMessage)
        )
            
    except Exception as e:
        error_msg = str(e).lower()
        logger.exception("Error in aggregator")
        if any(kw in error_msg for kw in ["api key", "authentication", "unauthorized", "401", "403", "invalid key", "missing credentials", "not set"]):
            return {"status": "error", "error": "API key is missing or invalid. Open settings to configure your API keys."}
        return {"status": "error", "error": f"Synthesis failed: {e!s}"}
    
    # Update the state with the final answer and set status to completed
    return {"final_answer": final_content, "status": "completed"}