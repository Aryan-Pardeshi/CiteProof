# SPDX-License-Identifier: MIT
"""Review protocol: PICO, eligibility criteria, planned sources and methods, with a completeness check.

Tables owned here: ``protocol`` (one row per field, JSON value) and ``protocol_changes``
(every change, so amendments made after the review started are on record, as PRISMA
2020 item 24c asks).
"""

import json
import sqlite3
from typing import Annotated, Any

from pydantic import Field

from citeproof_mcp import store
from citeproof_mcp.compat import MCPServer
from citeproof_mcp.tools.common import choice_arg, list_arg, safe_tool, text_arg

store.register_schema(
    (
        """CREATE TABLE IF NOT EXISTS protocol (
            field TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS protocol_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            field TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            changed_at TEXT NOT NULL
        )""",
    )
)

TEXT_FIELDS = ("population", "intervention", "comparator", "search_strategy", "registration", "synthesis_method")
LIST_FIELDS = ("outcomes", "study_designs", "inclusion_criteria", "exclusion_criteria", "databases", "languages")
ROB_TOOL_CHOICES = ("rob2", "robins_i", "nos", "nos_case_control", "mixed", "none")
REQUIRED = (
    "population",
    "intervention",
    "outcomes",
    "inclusion_criteria",
    "exclusion_criteria",
    "databases",
    "search_strategy",
)
RECOMMENDED = ("comparator", "study_designs", "year_range", "languages", "registration", "rob_tool", "synthesis_method")
ALL_FIELDS = TEXT_FIELDS + LIST_FIELDS + ("rob_tool",)

ListArg = list[str] | str | None


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def get_protocol(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every protocol field (empty string / list when unset) plus year_min and year_max."""
    stored = {r["field"]: json.loads(r["value"]) for r in conn.execute("SELECT field, value FROM protocol")}
    out: dict[str, Any] = {}
    for f in ALL_FIELDS:
        out[f] = stored.get(f, [] if f in LIST_FIELDS else "")
    meta = store.read_meta(conn)
    out["year_min"] = meta.get("year_min")
    out["year_max"] = meta.get("year_max")
    return out


def protocol_state(conn: sqlite3.Connection) -> dict[str, Any]:
    proto = get_protocol(conn)

    def empty(name: str) -> bool:
        if name == "year_range":
            return proto["year_min"] is None and proto["year_max"] is None
        return not proto[name]

    missing_required = [f for f in REQUIRED if empty(f)]
    return {
        "protocol": proto,
        "complete": not missing_required,
        "missing_required": missing_required,
        "missing_recommended": [f for f in RECOMMENDED if empty(f)],
    }


def protocol_changes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT field, old_value, new_value, changed_at FROM protocol_changes ORDER BY id"
    ).fetchall()
    return [
        {
            "field": r["field"],
            "old": json.loads(r["old_value"]) if r["old_value"] is not None else None,
            "new": json.loads(r["new_value"]) if r["new_value"] is not None else None,
            "changed_at": r["changed_at"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


def _desc(text: str) -> Any:
    return Field(description=text + " Omit to keep the stored value; send '' or [] to clear it.")


@safe_tool
def protocol_record(
    review_id: Annotated[str, Field(description="The id returned by review_create.")],
    population: Annotated[str | None, _desc("Population / participants (P).")] = None,
    intervention: Annotated[str | None, _desc("Intervention or exposure (I).")] = None,
    comparator: Annotated[str | None, _desc("Comparator (C), if any.")] = None,
    outcomes: Annotated[ListArg, _desc("Outcomes (O), primary first.")] = None,
    study_designs: Annotated[ListArg, _desc("Eligible study designs, e.g. ['RCT', 'prospective cohort'].")] = None,
    inclusion_criteria: Annotated[ListArg, _desc("Inclusion criteria, one per item.")] = None,
    exclusion_criteria: Annotated[ListArg, _desc("Exclusion criteria, one per item; reuse these as screening exclusion reasons.")] = None,
    databases: Annotated[ListArg, _desc("Databases and registers to search, e.g. ['PubMed', 'Embase'].")] = None,
    search_strategy: Annotated[str | None, _desc("Planned search strategy: concepts, key terms and the master query.")] = None,
    languages: Annotated[ListArg, _desc("Language limits, e.g. ['English'].")] = None,
    registration: Annotated[str | None, _desc("Protocol registration, e.g. 'PROSPERO CRD42024000000', or 'not registered'.")] = None,
    rob_tool: Annotated[str | None, _desc("Planned risk-of-bias tool: rob2, robins_i, nos, nos_case_control, mixed or none.")] = None,
    synthesis_method: Annotated[str | None, _desc("Planned synthesis, e.g. 'random-effects meta-analysis' or 'narrative synthesis (SWiM)'.")] = None,
    year_min: Annotated[int | None, Field(description="Earliest publication year. Omit to keep the stored value.")] = None,
    year_max: Annotated[int | None, Field(description="Latest publication year. Omit to keep the stored value.")] = None,
) -> dict[str, Any]:
    """Record or update the review protocol and check that it is complete. Call right after review_create.

    Send only the fields you want to set; omitted fields keep their stored value, so
    calling with just review_id reads the protocol back. List fields accept a list,
    or one string split on new lines and semicolons. Every change is logged; changes
    to a field that already had a value count as amendments (report them, PRISMA
    item 24c).

    Returns: protocol (every field), updated_fields, complete (all required fields
    set), missing_required (population, intervention, outcomes, inclusion_criteria,
    exclusion_criteria, databases, search_strategy), missing_recommended
    (comparator, study_designs, year_range, languages, registration, rob_tool,
    synthesis_method), amendments (changes to fields that already had a value), next_step.

    Does NOT judge whether the criteria are sensible; it checks they are written down.
    """
    incoming: dict[str, Any] = {}
    for name, value in (
        ("population", population),
        ("intervention", intervention),
        ("comparator", comparator),
        ("search_strategy", search_strategy),
        ("registration", registration),
        ("synthesis_method", synthesis_method),
    ):
        if value is not None:
            incoming[name] = text_arg(value, name, required=False, max_len=20_000 if name == "search_strategy" else 2000) or ""
    for name, value in (
        ("outcomes", outcomes),
        ("study_designs", study_designs),
        ("inclusion_criteria", inclusion_criteria),
        ("exclusion_criteria", exclusion_criteria),
        ("databases", databases),
        ("languages", languages),
    ):
        if value is not None:
            incoming[name] = list_arg(value, name, max_items=50, max_len=1000)
    if rob_tool is not None:
        incoming["rob_tool"] = choice_arg(rob_tool, "rob_tool", ROB_TOOL_CHOICES) if str(rob_tool).strip() else ""

    now = store.now_iso()
    updated: list[str] = []
    with store.review_db(review_id, write=True) as conn:
        current = get_protocol(conn)
        for name, value in incoming.items():
            if value == current[name]:
                continue
            old = current[name]
            conn.execute(
                "INSERT INTO protocol (field, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(field) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (name, json.dumps(value), now),
            )
            conn.execute(
                "INSERT INTO protocol_changes (field, old_value, new_value, changed_at) VALUES (?, ?, ?, ?)",
                (name, json.dumps(old) if old else None, json.dumps(value), now),
            )
            updated.append(name)
        if year_min is not None or year_max is not None:
            new_min = year_min if year_min is not None else current["year_min"]
            new_max = year_max if year_max is not None else current["year_max"]
            if (new_min, new_max) != (current["year_min"], current["year_max"]):
                store.set_year_range(conn, new_min, new_max)
                conn.execute(
                    "INSERT INTO protocol_changes (field, old_value, new_value, changed_at) VALUES (?, ?, ?, ?)",
                    (
                        "year_range",
                        json.dumps([current["year_min"], current["year_max"]])
                        if current["year_min"] is not None or current["year_max"] is not None
                        else None,
                        json.dumps([new_min, new_max]),
                        now,
                    ),
                )
                updated.append("year_range")
        state = protocol_state(conn)
        amendments = int(
            conn.execute("SELECT COUNT(*) FROM protocol_changes WHERE old_value IS NOT NULL").fetchone()[0]
        )
    if state["missing_required"]:
        step = f"Fill the missing required fields: {', '.join(state['missing_required'])}."
    else:
        step = "Protocol complete. Run the searches and log each one with prisma_record_search."
    return {"ok": True, **state, "updated_fields": updated, "amendments": amendments, "next_step": step}


def register(server: MCPServer) -> None:
    server.tool()(protocol_record)
