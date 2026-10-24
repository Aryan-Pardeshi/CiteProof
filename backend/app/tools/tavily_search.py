# Tavily tool wrapper
import os

from dotenv import load_dotenv
from langchain_core.tools import tool
from tavily import TavilyClient

load_dotenv()

tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

# Models sometimes send these as strings when a value is not set.
_UNSET_VALUES = frozenset({"", "none", "null"})
_TIME_RANGES = frozenset({"day", "month", "week", "year"})


def _normalize_search_topic(value: list[str] | str | None) -> list[str]:
    """Return a clean list; ``None``, ``""``, ``"none"`` and ``"null"`` mean unset."""
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [] if cleaned.lower() in _UNSET_VALUES else [cleaned]
    if isinstance(value, (list, tuple, set)):
        topics = []
        for item in value:
            cleaned = str(item).strip()
            if cleaned and cleaned.lower() not in _UNSET_VALUES:
                topics.append(cleaned)
        return topics
    return []


def _normalize_time_range(value: str | None) -> str | None:
    """Return one of day/month/week/year, or ``None`` for an unset/invalid value."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    if cleaned in _UNSET_VALUES or cleaned not in _TIME_RANGES:
        return None
    return cleaned


@tool
def search_web(
    query: str,
    search_topic: list[str] | str | None = None,
    time_range: str | None = None,
) -> str:
    """Use Tavily search to search the web for the given query.

    ``search_topic`` accepts a list or a single string. ``time_range`` accepts
    ``day``, ``month``, ``week`` or ``year``; omit it for historical queries.
    """
    topics = _normalize_search_topic(search_topic)
    if topics:
        query += f" Use sources: {', '.join(topics)}"
    try:
        response = tavily_client.search(
            time_range=_normalize_time_range(time_range),
            query=query,
            max_results=4,
            include_images=False,
            include_raw_content=False,
            search_depth="advanced",
        )
        return str(response)

    except Exception as e:  # noqa: BLE001
        return f"Error using Tavily search: {e!s}"
