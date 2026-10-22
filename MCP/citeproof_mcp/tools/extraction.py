# SPDX-License-Identifier: MIT
"""Evidence table: one structured data-extraction row per included study and outcome.

Every row carries the source quote it was extracted from. The server checks the
quote against the supplied source text, checks that each number entered
(sample size, effect estimate, CI bounds) is written in the quote, and checks the
arithmetic of the estimate and its interval.

Table owned here: ``evidence_rows``.
"""

import csv
import hashlib
import io
import json
import re
import sqlite3
import unicodedata
from typing import Annotated, Any

from pydantic import Field

from citeproof_mcp import store
from citeproof_mcp.compat import MCPServer
from citeproof_mcp.tools import prisma, source_tracking
from citeproof_mcp.tools.common import (
    MAX_TEXT_CHARS,
    cap,
    choice_arg,
    fail,
    float_arg,
    int_arg,
    md_table,
    paper_ref_arg,
    safe_tool,
    size_arg,
    text_arg,
)
from citeproof_mcp.tools.quotes import check_quote, numbers_missing

store.register_schema(
    (
        """CREATE TABLE IF NOT EXISTS evidence_rows (
            paper_ref TEXT NOT NULL,
            outcome_key TEXT NOT NULL,
            outcome TEXT NOT NULL,
            study_design TEXT NOT NULL,
            sample_size INTEGER,
            population TEXT,
            intervention TEXT,
            comparator TEXT,
            timepoint TEXT,
            effect_measure TEXT,
            effect_estimate REAL,
            ci_lower REAL,
            ci_upper REAL,
            ci_level REAL,
            notes TEXT,
            quote TEXT NOT NULL,
            quote_verified INTEGER NOT NULL,
            numbers_not_in_quote TEXT NOT NULL,
            source_chars INTEGER NOT NULL,
            source_sha256 TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (paper_ref, outcome_key)
        )""",
    )
)

EFFECT_MEASURES = ("rr", "or", "hr", "irr", "rom", "rd", "md", "smd", "other")
RATIO_MEASURES = ("rr", "or", "hr", "irr", "rom")
MEASURE_ALIASES = {
    "risk_ratio": "rr",
    "relative_risk": "rr",
    "odds_ratio": "or",
    "hazard_ratio": "hr",
    "incidence_rate_ratio": "irr",
    "rate_ratio": "irr",
    "ratio_of_means": "rom",
    "risk_difference": "rd",
    "mean_difference": "md",
    "standardised_mean_difference": "smd",
    "standardized_mean_difference": "smd",
    "hedges_g": "smd",
}
CSV_COLUMNS = (
    "paper_ref", "study_design", "sample_size", "population", "intervention", "comparator", "outcome",
    "timepoint", "effect_measure", "effect_estimate", "ci_lower", "ci_upper", "ci_level", "quote",
    "quote_verified", "numbers_not_in_quote", "notes",
)

ReviewId = Annotated[str, Field(description="The id returned by review_create.")]


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def evidence_rows(conn: sqlite3.Connection, paper_ref: str | None = None) -> list[dict[str, Any]]:
    sql = f"SELECT {', '.join(CSV_COLUMNS)} FROM evidence_rows"
    params: list[Any] = []
    if paper_ref:
        sql += " WHERE paper_ref = ?"
        params.append(paper_ref)
    rows = []
    for r in conn.execute(sql + " ORDER BY paper_ref, outcome_key", params):
        d = dict(r)
        d["quote_verified"] = bool(d["quote_verified"])
        d["numbers_not_in_quote"] = json.loads(d["numbers_not_in_quote"])
        rows.append(d)
    return rows


def _fmt(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:g}"


def format_effect(row: dict[str, Any]) -> str:
    if row.get("effect_estimate") is None:
        return ""
    text = f"{(row.get('effect_measure') or '').upper()} {_fmt(row['effect_estimate'])}".strip()
    if row.get("ci_lower") is not None:
        text += f" ({_fmt(row.get('ci_level') or 95)}% CI {_fmt(row['ci_lower'])} to {_fmt(row['ci_upper'])})"
    return text


def to_markdown(rows: list[dict[str, Any]]) -> str:
    header = ["Study", "Design", "N", "Population", "Intervention", "Comparator", "Outcome", "Effect", "Source quote", "Checks"]
    body: list[list[Any]] = []
    for r in rows:
        checks = "quote verified" if r["quote_verified"] else "QUOTE NOT VERIFIED"
        if r["numbers_not_in_quote"]:
            checks += "; not in quote: " + ", ".join(r["numbers_not_in_quote"])
        outcome = r["outcome"] + (f" ({r['timepoint']})" if r["timepoint"] else "")
        body.append(
            [
                r["paper_ref"], r["study_design"], r["sample_size"], r["population"], r["intervention"],
                r["comparator"], outcome, format_effect(r), r["quote"], checks,
            ]
        )
    return md_table(header, body)


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: Any) -> Any:
    """Stop spreadsheet formula injection in text cells (numbers are left as numbers).

    The first character is compared after Unicode NFKC, so full-width forms such as
    ``＝``, ``＋``, ``－`` and ``＠`` (which some spreadsheet locales read as formulas)
    are guarded like their ASCII versions.
    """
    if isinstance(value, str) and unicodedata.normalize("NFKC", value[:1]) in _FORMULA_PREFIXES:
        return "'" + value
    return value


def to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        out = dict(r)
        out["numbers_not_in_quote"] = ";".join(r["numbers_not_in_quote"])
        writer.writerow([_csv_safe(out[c]) if out[c] is not None else "" for c in CSV_COLUMNS])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@safe_tool
def evidence_record(
    review_id: ReviewId,
    paper_ref: Annotated[str, Field(description="Paper key: a DOI, 'pmid:<digits>' or 'title:<title>|<year>'. Should be a study included at full_text.")],
    outcome: Annotated[str, Field(description="Outcome this row reports, e.g. 'all-cause mortality'. One row per paper + outcome.")],
    study_design: Annotated[str, Field(description="Design as reported, e.g. 'RCT', 'prospective cohort', 'case-control'.")],
    quote: Annotated[str, Field(description="Verbatim passage from source_text holding the extracted data, including the numbers you enter. Use '...' to join separate parts.")],
    source_text: Annotated[str, Field(description="The text the quote comes from (max 400000 chars). Only its length and hash are stored.")],
    sample_size: Annotated[int | None, Field(description="Participants analysed for this outcome.")] = None,
    population: Annotated[str | None, Field(description="Who was studied.")] = None,
    intervention: Annotated[str | None, Field(description="Intervention or exposure.")] = None,
    comparator: Annotated[str | None, Field(description="Comparator.")] = None,
    timepoint: Annotated[str | None, Field(description="When the outcome was measured, e.g. '12 months'.")] = None,
    effect_measure: Annotated[
        str | None,
        Field(description="rr, or, hr, irr, rom (ratios), rd, md, smd (differences) or other. Required with effect_estimate."),
    ] = None,
    effect_estimate: Annotated[float | str | None, Field(description="Point estimate as reported.")] = None,
    ci_lower: Annotated[float | str | None, Field(description="Lower confidence limit.")] = None,
    ci_upper: Annotated[float | str | None, Field(description="Upper confidence limit.")] = None,
    ci_level: Annotated[float | str | None, Field(description="Confidence level in percent; default 95 when a CI is given.")] = None,
    notes: Annotated[str | None, Field(description="Anything a reader needs, e.g. 'adjusted for age and sex'.")] = None,
) -> dict[str, Any]:
    """Record one evidence-table row (one study, one outcome) with its source quote checked.

    Call during data extraction for each included study and outcome. Re-sending
    the same paper_ref + outcome replaces the row. Checks: the quote must appear in
    source_text (quote_verified); every number you enter (sample_size,
    effect_estimate, ci_lower, ci_upper) must be written in the quote
    (numbers_not_in_quote lists those that are not); the CI must contain the
    estimate; ratio measures must be positive. Arithmetic errors are refused;
    quote and number mismatches are stored and flagged.

    Returns: updated, paper_ref, outcome, quote_verified, quote_note,
    numbers_not_in_quote, effect (formatted, e.g. 'RR 0.82 (95% CI 0.7 to 0.96)'),
    rows_for_paper, warnings.

    Does NOT extract anything itself: every value is yours.
    """
    ref = paper_ref_arg(paper_ref)
    outcome_clean = text_arg(outcome, "outcome", max_len=300)
    design = text_arg(study_design, "study_design", max_len=200)
    quote_clean = text_arg(quote, "quote", max_len=4000)
    source = size_arg(source_text, "source_text", max_len=MAX_TEXT_CHARS)
    if not source.strip():
        fail("source_text is required so the quote and numbers can be checked")
    n = int_arg(sample_size, "sample_size", required=False, min_value=1, max_value=100_000_000)
    est = float_arg(effect_estimate, "effect_estimate")
    lo = float_arg(ci_lower, "ci_lower")
    hi = float_arg(ci_upper, "ci_upper")
    level = float_arg(ci_level, "ci_level")
    measure = (
        choice_arg(effect_measure, "effect_measure", EFFECT_MEASURES, MEASURE_ALIASES)
        if effect_measure is not None and str(effect_measure).strip()
        else None
    )
    if est is not None and measure is None:
        fail("effect_measure is required with effect_estimate (rr, or, hr, irr, rom, rd, md, smd or other)")
    if (lo is None) != (hi is None):
        fail("give both ci_lower and ci_upper, or neither")
    if lo is not None:
        if est is None:
            fail("a confidence interval needs effect_estimate")
        if lo > hi:
            fail(f"ci_lower ({lo:g}) is greater than ci_upper ({hi:g})")
        if not (lo <= est <= hi):
            fail(f"effect_estimate {est:g} lies outside its confidence interval {lo:g} to {hi:g}")
        level = 95.0 if level is None else level
        if not (50 < level < 100):
            fail("ci_level must be a percentage between 50 and 100, e.g. 95")
    else:
        level = None
    if measure in RATIO_MEASURES and any(v is not None and v <= 0 for v in (est, lo, hi)):
        fail(f"{measure.upper()} is a ratio: the estimate and CI limits must be greater than 0")

    quote_verified, quote_note = check_quote(quote_clean, source)
    missing = numbers_missing(
        {"sample_size": float(n) if n is not None else None, "effect_estimate": est, "ci_lower": lo, "ci_upper": hi},
        quote_clean,
    )
    warnings: list[str] = []
    if not quote_verified:
        warnings.append("Quote was not found in the supplied source text.")
    if missing:
        warnings.append(
            f"Not written in the quote: {', '.join(missing)}. Quote the passage that states these numbers, or correct them."
        )

    row = {
        "paper_ref": ref,
        "outcome_key": re.sub(r"\s+", " ", outcome_clean.lower()),
        "outcome": outcome_clean,
        "study_design": design,
        "sample_size": n,
        "population": text_arg(population, "population", required=False, max_len=1000),
        "intervention": text_arg(intervention, "intervention", required=False, max_len=1000),
        "comparator": text_arg(comparator, "comparator", required=False, max_len=1000),
        "timepoint": text_arg(timepoint, "timepoint", required=False, max_len=200),
        "effect_measure": measure,
        "effect_estimate": est,
        "ci_lower": lo,
        "ci_upper": hi,
        "ci_level": level,
        "notes": text_arg(notes, "notes", required=False, max_len=2000),
        "quote": quote_clean,
        "quote_verified": int(quote_verified),
        "numbers_not_in_quote": json.dumps(missing),
        "source_chars": len(source),
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "updated_at": store.now_iso(),
    }
    cols = list(row)
    with store.review_db(review_id, write=True) as conn:
        existing = conn.execute(
            "SELECT source_sha256, source_chars FROM evidence_rows WHERE paper_ref = ? AND outcome_key = ?",
            (ref, row["outcome_key"]),
        ).fetchone()
        fb_sha = existing["source_sha256"] if existing else None
        fb_len = existing["source_chars"] if existing else None

        conn.execute(
            f"INSERT INTO evidence_rows ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
            "ON CONFLICT(paper_ref, outcome_key) DO UPDATE SET "
            + ", ".join(f"{c} = excluded.{c}" for c in cols if c not in ("paper_ref", "outcome_key")),
            [row[c] for c in cols],
        )
        included = prisma.included_refs(conn)
        rows_for_paper = int(
            conn.execute("SELECT COUNT(*) FROM evidence_rows WHERE paper_ref = ?", (ref,)).fetchone()[0]
        )
        source_changed = source_tracking.record_observation(
            conn,
            ref,
            "evidence_record",
            row["outcome_key"],
            source,
            fallback_sha256=fb_sha,
            fallback_length=fb_len,
        )
    if included and ref not in included:
        warnings.append(f"'{ref}' is not among the studies included at full_text; check the paper_ref or the screening.")
    if source_changed:
        warnings.append(source_tracking.format_change_warning(source_changed))
    res = {
        "ok": True,
        "updated": existing is not None,
        "paper_ref": ref,
        "outcome": outcome_clean,
        "quote_verified": bool(quote_verified),
        "quote_note": quote_note,
        "numbers_not_in_quote": missing,
        "effect": format_effect(row),
        "rows_for_paper": rows_for_paper,
        "warnings": warnings,
    }
    if source_changed:
        res["source_changed"] = source_changed
    return res


@safe_tool
def evidence_table(
    review_id: ReviewId,
    format: Annotated[str, Field(description="'markdown' (table for the report), 'csv' (all columns, for a spreadsheet) or 'json' (rows).")] = "markdown",
    paper_ref: Annotated[str | None, Field(description="Only this paper's rows.")] = None,
) -> dict[str, Any]:
    """Export the evidence table (all rows recorded with evidence_record) and report extraction gaps.

    markdown and csv return the whole table as text in `table` (save it yourself);
    json returns `rows` (capped at 200, see truncated). Text cells are escaped for
    the format; CSV text cells starting with = + - @ are prefixed with ' so a
    spreadsheet does not run them as formulas.

    Returns: format, row_count, table or rows, truncated, included_without_rows
    (included studies with no row yet, first 50) and its count
    included_without_rows_count, rows_quote_not_verified, rows_numbers_not_in_quote.
    """
    fmt = choice_arg(format, "format", ("markdown", "csv", "json"))
    ref = paper_ref_arg(paper_ref) if paper_ref is not None and str(paper_ref).strip() else None
    with store.review_db(review_id) as conn:
        rows = evidence_rows(conn, ref)
        included = prisma.included_refs(conn)
    have = {r["paper_ref"] for r in rows}
    gaps = [p for p in included if p not in have] if ref is None else []
    gaps_listed, _ = cap(gaps, 50)
    result: dict[str, Any] = {"ok": True, "format": fmt, "row_count": len(rows)}
    if fmt == "json":
        listed, truncated = cap(rows)
        result.update(rows=listed, truncated=truncated)
    else:
        result.update(table=to_markdown(rows) if fmt == "markdown" else to_csv(rows), truncated=False)
    result.update(
        included_without_rows=gaps_listed,
        included_without_rows_count=len(gaps),
        rows_quote_not_verified=sum(1 for r in rows if not r["quote_verified"]),
        rows_numbers_not_in_quote=sum(1 for r in rows if r["numbers_not_in_quote"]),
    )
    return result


def register(server: MCPServer) -> None:
    server.tool()(evidence_record)
    server.tool()(evidence_table)
