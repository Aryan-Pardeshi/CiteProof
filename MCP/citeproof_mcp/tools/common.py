# SPDX-License-Identifier: MIT
"""Helpers shared by every tool module: the never-raise wrapper, argument checks, list caps.

Tool modules do not use ``from __future__ import annotations``: the MCP SDK reads
the tool signatures (through ``functools.wraps``) to build each JSON schema, and
real annotation objects work the same way in mcp 1.x and 2.x.
"""

import functools
import inspect
import logging
import math
import re
import sqlite3
from collections.abc import Callable, Sequence
from typing import Any

from citeproof_mcp import store

logger = logging.getLogger("citeproof_mcp")

LIST_CAP = 200
MAX_TEXT_CHARS = 400_000


class BadInput(store.StoreError):
    """Invalid tool argument. The message tells the agent what to send instead."""


def fail(message: str) -> None:
    raise BadInput(message)


# ---------------------------------------------------------------------------
# Never-raise wrapper
# ---------------------------------------------------------------------------


def _error_result(name: str, exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, store.StoreError):
        return {"ok": False, "error": str(exc)}
    if isinstance(exc, sqlite3.OperationalError) and any(
        word in str(exc).lower() for word in ("locked", "busy")
    ):
        return {
            "ok": False,
            "error": "the review file is busy with another write; nothing was saved. Retry the call.",
        }
    logger.exception("tool %s failed", name)
    return {
        "ok": False,
        "error": (
            f"internal error in {name} ({type(exc).__name__}); nothing was saved. "
            "Check the arguments against the tool description and retry."
        ),
    }


def safe_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Turn any exception into ``{"ok": false, "error": ...}``.

    ``StoreError``/``BadInput`` messages are passed through; anything else becomes a
    short message with no stack trace and no file paths (details go to the log).
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - tools never raise
                return _error_result(fn.__name__, exc)

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - tools never raise
            return _error_result(fn.__name__, exc)

    return wrapper


# ---------------------------------------------------------------------------
# Argument checks. Each returns the cleaned value or raises BadInput.
# ---------------------------------------------------------------------------


def text_arg(
    value: Any,
    name: str,
    *,
    required: bool = True,
    max_len: int = 2000,
) -> str | None:
    """Stripped string; None when optional and empty."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            fail(f"{name} is required and must not be empty")
        return None
    if not isinstance(value, str):
        fail(f"{name} must be a string")
    clean = value.strip()
    if len(clean) > max_len:
        fail(f"{name} is {len(clean)} characters; the limit is {max_len}")
    return clean


def size_arg(value: Any, name: str, *, max_len: int = MAX_TEXT_CHARS) -> str:
    """A possibly empty block of text (source text, report text). Not stripped."""
    if value is None:
        return ""
    if not isinstance(value, str):
        fail(f"{name} must be a string")
    if len(value) > max_len:
        fail(f"{name} is {len(value)} characters; the limit is {max_len}. Send the relevant section only.")
    return value


def _key(value: str) -> str:
    return re.sub(r"[\s\-]+", "_", value.strip().lower())


def choice_arg(
    value: Any,
    name: str,
    allowed: Sequence[str],
    aliases: dict[str, str] | None = None,
) -> str:
    """One of ``allowed``; case, spaces and hyphens are forgiven ('Some concerns' -> 'some_concerns')."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        fail(f"{name} must be one of: {', '.join(allowed)}")
    key = _key(str(value))
    if aliases and key in aliases:
        key = aliases[key]
    if key not in allowed:
        fail(f"{name} must be one of: {', '.join(allowed)}; got '{str(value)[:60]}'")
    return key


def int_arg(
    value: Any,
    name: str,
    *,
    required: bool = True,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int | None:
    """Whole number. Accepts 12, 12.0 and '12'; rejects booleans, 12.5 and text."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            fail(f"{name} is required")
        return None
    result: int | None = None
    if isinstance(value, bool):
        result = None
    elif isinstance(value, int):
        result = value
    elif isinstance(value, float) and value.is_integer() or isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
        result = int(value)
    if result is None:
        fail(f"{name} must be a whole number")
    if min_value is not None and result < min_value:
        fail(f"{name} must be at least {min_value}")
    if max_value is not None and result > max_value:
        fail(f"{name} must be at most {max_value}")
    return result


def float_arg(value: Any, name: str) -> float | None:
    """Finite number or None. Accepts numeric strings; a Unicode minus sign is read as '-'."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        fail(f"{name} must be a number")
    try:
        result = float(value.replace("−", "-").strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        fail(f"{name} must be a number")
    if not math.isfinite(result):
        fail(f"{name} must be a finite number")
    return result


def list_arg(
    value: Any,
    name: str,
    *,
    max_items: int = 50,
    max_len: int = 1000,
) -> list[str]:
    """List of non-empty strings. A single string is split on new lines and semicolons."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [part for part in re.split(r"[\n;]", value)]
    if not isinstance(value, (list, tuple)):
        fail(f"{name} must be a list of strings")
    out: list[str] = []
    for item in value:
        if item is None:
            continue
        if not isinstance(item, (str, int, float)) or isinstance(item, bool):
            fail(f"{name} must be a list of strings")
        clean = str(item).strip()
        if not clean:
            continue
        if len(clean) > max_len:
            fail(f"an item in {name} is {len(clean)} characters; the limit is {max_len}")
        if clean not in out:
            out.append(clean)
    if len(out) > max_items:
        fail(f"{name} has {len(out)} items; the limit is {max_items}")
    return out


def reviewer_arg(value: Any, name: str = "reviewer", *, default: str | None = "primary") -> str:
    """Reviewer name: 1 to 40 characters, stored lower-case."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is None:
            fail(f"{name} is required")
        return default  # type: ignore[return-value]
    if not isinstance(value, str) or len(value.strip()) > 40:
        fail(f"{name} must be a name of 1 to 40 characters")
    return value.strip().lower()


def paper_ref_arg(value: Any) -> str:
    return store.canonical_paper_ref(value)


def cap(items: list[Any], limit: int = LIST_CAP) -> tuple[list[Any], bool]:
    """First ``limit`` items and whether anything was cut."""
    return items[:limit], len(items) > limit


def md_cell(value: Any, limit: int = 300) -> str:
    """One Markdown table cell: single line, pipes escaped, no raw HTML, at most ``limit`` characters."""
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def md_table(headers: Sequence[str], rows: list[Sequence[Any]], limit: int = 300) -> str:
    """Markdown table text; every cell goes through :func:`md_cell`."""
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(md_cell(c, limit) for c in row) + " |" for row in rows]
    return "\n".join(lines)
