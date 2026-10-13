# Tavily tool wrapper
import os
from typing import List, Literal, Optional

from dotenv import load_dotenv
from langchain_core.tools import tool
from tavily import TavilyClient

load_dotenv()

tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))


@tool
def search_web(
    query: str,
    search_topic: Optional[List[str]] = None,
    time_range: Optional[Literal["day", "month", "week", "year"]] = None,
) -> str:
    """Use Tavily search to search the web for the given query."""
    if search_topic:
        query += f" Use sources: {', '.join(search_topic)}"
    try:
        response = tavily_client.search(
            time_range=time_range,
            query=query,
            max_results=4,
            include_images=False,
            include_raw_content=False,
            search_depth="advanced",
        )
        return str(response)

    except Exception as e:
        return f"Error using Tavily search: {str(e)}"
