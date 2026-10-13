import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_community.cache import SQLiteCache
from langchain_core.globals import set_llm_cache
from langchain_openai import ChatOpenAI

load_dotenv()
logger = logging.getLogger(__name__)


class LoggingSQLiteCache(SQLiteCache):
    def lookup(self, prompt: str, llm_string: str):
        result = super().lookup(prompt, llm_string)
        prefix = (prompt[:50] + "...") if len(prompt) > 50 else prompt
        if result is not None:
            logger.info(f"[LLM CACHE HIT] Prompt: {prefix!r}")
        else:
            logger.info(f"[LLM CACHE MISS] Prompt: {prefix!r}")
        return result


def init_llm_cache():
    try:
        cache_path_str = os.getenv("LLM_CACHE_PATH", "./data/llm_cache.db")
        cache_path = Path(cache_path_str).resolve()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        set_llm_cache(LoggingSQLiteCache(database_path=str(cache_path)))
        logger.info(f"LLM cache initialized at {cache_path}")
    except Exception as e:
        logger.warning(f"Failed to initialize SQLite LLM cache: {e}")


init_llm_cache()


def get_llm_config():
    api_key = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    base_url = os.getenv("LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    return api_key, base_url


def get_llm(model: str | None = None, role: str | None = None, temperature: float = 0.0) -> ChatOpenAI:
    api_key, base_url = get_llm_config()
    if not api_key or api_key == "your_api_key_here" or api_key == "your_key_here":
        raise RuntimeError(
            "LLM_API_KEY is not set. "
            "Set it in the .env file or via the settings modal."
        )

    if not model:
        if role == "planner":
            model = os.getenv("LLM_MODEL_PLANNER", "deepseek-chat")
        elif role == "researcher":
            model = os.getenv("LLM_MODEL_RESEARCHER", "deepseek-chat")
        elif role == "aggregator":
            model = os.getenv("LLM_MODEL_AGGREGATOR", "deepseek-chat")
        else:
            model = os.getenv("LLM_MODEL_PLANNER", "deepseek-chat")

    timeout_val = float(os.getenv("LLM_REQUEST_TIMEOUT", "60.0"))
    return ChatOpenAI(
        model=model,
        openai_api_key=api_key,
        openai_api_base=base_url,
        temperature=temperature,
        request_timeout=timeout_val,
        max_retries=0
    )


class _LazyLLM:
    def __init__(self, role: str | None = None, model_override: str | None = None):
        self._role = role
        self._model_override = model_override
        self._instance = None

    def _get(self):
        if self._instance is None:
            self._instance = get_llm(model=self._model_override, role=self._role)
        return self._instance

    def reset(self):
        self._instance = None

    def __getattr__(self, name):
        return getattr(self._get(), name)


llm = _LazyLLM()
lazy_llm = llm
llm_fast = _LazyLLM(role="researcher")
llm_pro = _LazyLLM(role="aggregator")


STRUCTURED_METHODS = ("json_schema", "function_calling", "json_mode")
_structured_method_cache: dict[tuple[str, str], str] = {}


def invoke_structured(base_llm, schema, prompt):
    """Invokes an LLM with structured output, negotiating the method per provider."""
    model = getattr(base_llm, "model_name", "?")
    base = str(getattr(base_llm, "openai_api_base", "") or "")
    key = (base, model)

    methods = STRUCTURED_METHODS
    cached = _structured_method_cache.get(key)
    if cached:
        methods = (cached,) + tuple(m for m in STRUCTURED_METHODS if m != cached)

    last_error = None
    for method in methods:
        try:
            result = base_llm.with_structured_output(schema, method=method).invoke(prompt)
            if result is None:
                last_error = ValueError(f"{method} returned no parsed output")
                continue
            if _structured_method_cache.get(key) != method:
                logger.info(f"Structured output via '{method}' for {model} at {base}")
                _structured_method_cache[key] = method
            return result
        except Exception as e:
            last_error = e
            logger.debug(f"Structured output method '{method}' failed for {model}: {e}")

    raise last_error if last_error else RuntimeError("Structured output failed")


async def ainvoke_structured_with_retry(
    base_llm,
    schema,
    prompt: str,
    max_retries: int = 2,
    strict: bool = False
):
    """Async structured output invocation with exponential backoff retry.
    
    strict=True enforces json_schema method only (for critical extraction agents).
    strict=False negotiates the full fallback chain (json_schema -> function_calling -> json_mode).
    """
    model = getattr(base_llm, "model_name", "?")
    base = str(getattr(base_llm, "openai_api_base", "") or "")
    key = (base, model)

    methods = ("json_schema",) if strict else STRUCTURED_METHODS
    cached = _structured_method_cache.get(key)
    if cached and not strict:
        methods = (cached,) + tuple(m for m in STRUCTURED_METHODS if m != cached)

    last_error = None
    start_time = asyncio.get_event_loop().time()
    total_deadline = 60.0  # seconds

    for attempt in range(max_retries + 1):
        if asyncio.get_event_loop().time() - start_time > total_deadline:
            break
        for method in methods:
            try:
                structured_chain = base_llm.with_structured_output(schema, method=method)
                result = await asyncio.to_thread(structured_chain.invoke, prompt)
                if result is not None:
                    if not strict and _structured_method_cache.get(key) != method:
                        _structured_method_cache[key] = method
                    return result
                last_error = ValueError(f"{method} returned empty structured output")
            except Exception as e:
                last_error = e
                logger.debug(f"Structured attempt {attempt+1}/{max_retries+1} ({method}) failed: {e}")
                err_str = str(e).lower()
                # If non-transient error like invalid parameter/schema, don't repeat full method matrix
                is_transient = any(kw in err_str for kw in ("timeout", "rate", "429", "500", "502", "503", "connection", "overloaded"))
                if not is_transient and strict:
                    break

        if attempt < max_retries:
            backoff = min(1.5 ** attempt, 5.0)
            await asyncio.sleep(backoff)

    raise last_error or RuntimeError("Structured output failed after retries")
