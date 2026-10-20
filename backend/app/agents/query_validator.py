# query validator node
import logging

from backend.app.graph.state import ResearchState

logger = logging.getLogger(__name__)


from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from backend.app.llm import invoke_structured, llm_fast

# System prompt for query validation
VALIDATOR_SYSTEM_PROMPT = """You are an input validation assistant for a specialized Research Bot.
Your task is to analyze the user's input query and determine if it is a valid, substantive, and safe research topic.

Criteria for a VALID research query:
- It asks to research, explain, analyze, or gather info on a specific topic (e.g., "Recent breakthroughs in Quantum Computing in 2026", "Impact of microplastics on marine life").
- Even if it starts with polite greetings or conversational filler (e.g., "Hello, can you please research...", "Hi, tell me about..."), it is VALID as long as it contains a specific subject of research.

Criteria for an INVALID query:
- It is just conversational greeting/filler (e.g., "hello", "hi there", "greetings").
- It is a general query about you or the bot (e.g., "who are you", "what is your name", "what can you do").
- It is a generic command without any topic (e.g., "do some research for me", "search something", "please start").
- It is empty, gibberish, or completely lacks any researchable subject.
- It contains inappropriate, harmful, unsafe, illegal, sensitive, or restricted content (e.g., self-harm, weapon creation, illegal drugs, cyberattacks, hate speech, explicit content). In this case, mark is_valid as false and set error_message to "Query contains inappropriate or restricted content."

You MUST respond in JSON matching this schema:
{{
  "is_valid": true or false,
  "error_message": "your error message here, or null if is_valid is true"
}}
"""

class QueryValidation(BaseModel):
    is_valid: bool = Field(
        description="True if the input query contains a specific research topic and is safe/appropriate to process. False if it is a greeting, chatbot meta-question, generic conversational filler, or inappropriate/unsafe/restricted content."
    )
    error_message: str | None = Field(
        default=None,
        description="If is_valid is False, provide a friendly error message explaining why (e.g. 'Query contains inappropriate or restricted content.' for unsafe queries) and ask the user to provide a specific research topic. Otherwise, leave empty."
    )

def query_validator(state: ResearchState) -> dict:
    query = state.get("query", "").strip()
    logger.info(f"Validating query: '{query}'")
    
    if not query:
        logger.warning("Query validation failed: Empty query.")
        return {"status": "error", "error": "Please provide a specific research topic."}
        
    # Pre-filter very short queries (less than 3 words) to save API tokens
    words = query.split()
    if len(words) < 4:
        logger.warning(f"Query validation failed: Query too short ({len(words)} words).")
        return {"status": "error", "error": "Not a valid research query. Please provide a specific research topic."}
        
    try:
        prompt = ChatPromptTemplate([
            ("system", VALIDATOR_SYSTEM_PROMPT),
            ("user", f"Input Query: {query}")
        ])
        messages = prompt.format_messages()
        validation = invoke_structured(llm_fast, QueryValidation, messages)
        
        if not validation.is_valid:
            error_msg = validation.error_message or "Not a valid research query. Please provide a specific research topic."
            logger.warning(f"Query validation failed (LLM): {error_msg}")
            return {"status": "error", "error": error_msg}
            
    except Exception as e:  # noqa: BLE001
        error_msg = str(e).lower()
        logger.error(f"Error during query validation LLM call: {e}")
        if any(kw in error_msg for kw in ["api key", "authentication", "unauthorized", "401", "403", "invalid key", "missing credentials", "not set"]):
            return {"status": "error", "error": "API key is missing or invalid. Open settings to configure your API keys."}
        # Fallback to a basic check if the LLM call fails
        words = query.split()
        if len(words) < 5:
            logger.warning(f"Query validation failed (Fallback check): Query under 5 words ({len(words)} words).")
            return {"status": "error", "error": "Not a valid research query. Please provide a specific research topic."}
            
    logger.info("Query validation successful.")
    return {}