# SPDX-License-Identifier: MIT
"""Claim-support records and risk-of-bias records.

The calling agent makes every judgement. This module validates the shape of each
judgement, checks quotes against the source text the agent supplies, stores the
record, and computes summaries with the published rules. It never stores the
source text itself, only its length and SHA-256 hash.

Tables owned here: ``claims``, ``rob``.
"""

import hashlib
import sqlite3
from collections import defaultdict
from typing import Annotated, Any

from pydantic import Field

from citeproof_mcp import store
from citeproof_mcp.compat import MCPServer
from citeproof_mcp.tools import source_tracking
from citeproof_mcp.tools.common import (
    MAX_TEXT_CHARS,
    cap,
    choice_arg,
    fail,
    paper_ref_arg,
    safe_tool,
    size_arg,
    text_arg,
)
from citeproof_mcp.tools.quotes import check_quote, normalize_text

store.register_schema(
    (
        """CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY,
            claim_key TEXT,
            claim_text TEXT,
            paper_ref TEXT,
            label TEXT,
            reason TEXT,
            quote TEXT,
            quote_verified INTEGER,
            source_chars INTEGER,
            source_sha256 TEXT,
            updated_at TEXT,
            UNIQUE (claim_key, paper_ref)
        )""",
        """CREATE TABLE IF NOT EXISTS rob (
            paper_ref TEXT,
            tool TEXT,
            domain TEXT,
            judgement TEXT,
            quote TEXT,
            quote_verified INTEGER,
            source_chars INTEGER,
            source_sha256 TEXT,
            updated_at TEXT,
            PRIMARY KEY (paper_ref, tool, domain)
        )""",
        "ALTER TABLE rob ADD COLUMN rationale TEXT",
    )
)

MAX_SOURCE_CHARS = MAX_TEXT_CHARS
CLAIM_LABELS: tuple[str, ...] = ("supported", "partial", "unsupported")

ROB2_DOMAINS: tuple[str, ...] = (
    "randomisation_process",
    "deviations_from_intended_interventions",
    "missing_outcome_data",
    "measurement_of_the_outcome",
    "selection_of_the_reported_result",
)
ROB2_JUDGEMENTS: tuple[str, ...] = ("low", "some_concerns", "high")

ROBINS_I_DOMAINS: tuple[str, ...] = (
    "confounding",
    "selection_of_participants",
    "classification_of_interventions",
    "deviations_from_intended_interventions",
    "missing_data",
    "measurement_of_outcomes",
    "selection_of_the_reported_result",
)
ROBINS_I_JUDGEMENTS: tuple[str, ...] = ("low", "moderate", "serious", "critical", "no_information")

NOS_STAR_RANGES: dict[str, dict[str, tuple[int, int]]] = {
    "nos": {"selection": (0, 4), "comparability": (0, 2), "outcome": (0, 3)},
    "nos_case_control": {"selection": (0, 4), "comparability": (0, 2), "exposure": (0, 3)},
}
NOS_DOMAINS: tuple[str, ...] = tuple(NOS_STAR_RANGES["nos"])

ROB_TOOLS: dict[str, tuple[str, ...]] = {
    "rob2": ROB2_DOMAINS,
    "robins_i": ROBINS_I_DOMAINS,
    "nos": NOS_DOMAINS,
    "nos_case_control": tuple(NOS_STAR_RANGES["nos_case_control"]),
}
SEVERITY: dict[str, dict[str, int]] = {
    "rob2": {"low": 1, "some_concerns": 2, "high": 3},
    "robins_i": {"low": 1, "moderate": 2, "serious": 3, "critical": 4},
}
DOMAIN_ALIASES: dict[str, dict[str, str]] = {
    "rob2": {f"d{i + 1}": d for i, d in enumerate(ROB2_DOMAINS)}
    | {"randomization_process": "randomisation_process", "measurement_of_outcome": "measurement_of_the_outcome"},
    "robins_i": {f"d{i + 1}": d for i, d in enumerate(ROBINS_I_DOMAINS)},
}
JUDGEMENT_ALIASES = {
    "low_risk": "low",
    "high_risk": "high",
    "moderate_risk": "moderate",
    "serious_risk": "serious",
    "critical_risk": "critical",
    "ni": "no_information",
}

OVERALL_RULES: dict[str, str] = {
    "rob2": (
        "low when every domain is low; some_concerns when any domain has some concerns and none is high; "
        "high when any domain is high. With some concerns in several domains RoB 2 lets the reviewer judge "
        "high: record domain 'overall' to do so. incomplete while a domain is missing."
    ),
    "robins_i": (
        "critical when any domain is critical; else serious when any is serious; else no_information when any "
        "domain lacks information (record domain 'overall' as low or moderate if those are not key domains); "
        "else moderate when any is moderate; else low. incomplete while a domain is missing."
    ),
    "nos": (
        "AHRQ thresholds: good = selection 3-4, comparability 1-2 and outcome 2-3 stars; fair = selection 2, "
        "comparability 1-2 and outcome 2-3; poor = anything else. incomplete while a domain is missing."
    ),
    "nos_case_control": "As nos, with the exposure domain in place of outcome.",
}

ReviewId = Annotated[str, Field(description="The id returned by review_create.")]
PaperRef = Annotated[
    str,
    Field(description="Paper key: a DOI ('10.1234/abc'), 'pmid:<digits>' or 'title:<title>|<year>'."),
]
SourceText = Annotated[
    str,
    Field(
        description=(
            "The text the quote comes from (abstract, section or full text; max 400000 chars). Used only "
            "to check the quote; only its length and SHA-256 hash are stored."
        )
    ),
]


def _source_fingerprint(source_text: str) -> tuple[int, str]:
    return len(source_text), hashlib.sha256(source_text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def claim_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id AS claim_id, claim_text, paper_ref, label, reason, quote, quote_verified, source_chars, "
        "source_sha256, updated_at FROM claims ORDER BY id"
    ).fetchall()
    return [dict(r) | {"quote_verified": bool(r["quote_verified"])} for r in rows]


def rob_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT paper_ref, tool, domain, judgement, quote, quote_verified, rationale, source_chars, "
        "source_sha256, updated_at FROM rob ORDER BY paper_ref, domain"
    ).fetchall()
    return [dict(r) | {"quote_verified": bool(r["quote_verified"])} for r in rows]


def claims_summary(conn: sqlite3.Connection, report_text: str | None = None) -> dict[str, Any]:
    """Claim counts and the lists claim_report returns (no 'ok' key)."""
    rows = conn.execute(
        "SELECT id, claim_text, paper_ref, label, reason, quote, quote_verified FROM claims ORDER BY id"
    ).fetchall()
    counts = {label: 0 for label in CLAIM_LABELS}
    suspicious: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    in_report: list[dict[str, Any]] = []
    quotes_not_verified = 0
    norm_report = normalize_text(report_text) if report_text else ""
    for r in rows:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
        has_quote = bool((r["quote"] or "").strip())
        if has_quote and not r["quote_verified"]:
            quotes_not_verified += 1
        entry = {
            "claim_id": r["id"],
            "claim_text": (r["claim_text"] or "")[:200],
            "paper_ref": r["paper_ref"],
            "label": r["label"],
            "reason": r["reason"],
        }
        weak = r["label"] == "unsupported" or (r["label"] in ("supported", "partial") and not r["quote_verified"])
        if r["label"] in ("supported", "partial") and not r["quote_verified"]:
            suspicious.append(entry)
        if r["label"] == "unsupported":
            unsupported.append(entry)
        if norm_report and weak and normalize_text(r["claim_text"] or "") in norm_report:
            in_report.append(entry)
    suspicious_l, cut_a = cap(suspicious)
    unsupported_l, cut_b = cap(unsupported)
    in_report_l, cut_c = cap(in_report)
    return {
        "total": len(rows),
        "counts_by_label": counts,
        "quotes_not_verified": quotes_not_verified,
        "suspicious_count": len(suspicious),
        "suspicious": suspicious_l,
        "unsupported_count": len(unsupported),
        "unsupported": unsupported_l,
        "weak_claims_in_report": in_report_l if report_text is not None else None,
        "truncated": cut_a or cut_b or cut_c,
    }


def _overall(tool: str, judgements: dict[str, str], recorded: str | None) -> tuple[str, str | None, list[str]]:
    """(overall, rule_result, notes) for one paper."""
    notes: list[str] = []
    domains = ROB_TOOLS[tool]
    missing = [d for d in domains if d not in judgements]
    if missing:
        return "incomplete", "incomplete", notes
    values = [judgements[d] for d in domains]

    if tool in NOS_STAR_RANGES:
        stars = {d: int(judgements[d]) for d in domains}
        last = domains[2]
        if stars["selection"] >= 3 and stars["comparability"] >= 1 and stars[last] >= 2:
            return "good", "good", notes
        if stars["selection"] == 2 and stars["comparability"] >= 1 and stars[last] >= 2:
            return "fair", "fair", notes
        return "poor", "poor", notes

    sev = SEVERITY[tool]
    allowed: list[str]
    if tool == "rob2":
        if "high" in values:
            rule, allowed = "high", ["high"]
        elif values.count("some_concerns") >= 2:
            rule, allowed = "some_concerns", ["some_concerns", "high"]
            notes.append(
                "Some concerns in several domains: RoB 2 allows an overall 'high' judgement if this substantially "
                "lowers confidence in the result. Record domain 'overall' to set it."
            )
        elif "some_concerns" in values:
            rule, allowed = "some_concerns", ["some_concerns"]
        else:
            rule, allowed = "low", ["low"]
    else:
        rated = [v for v in values if v != "no_information"]
        worst = max(rated, key=lambda v: sev[v]) if rated else None
        if worst in ("critical", "serious"):
            rule, allowed = worst, [worst]
        elif worst is None:
            rule, allowed = "no_information", ["no_information"]
        elif len(rated) < len(values):
            rule = "no_information"
            floor = sev[worst]
            allowed = ["no_information"] + [lvl for lvl in ("low", "moderate") if sev[lvl] >= floor]
            notes.append(
                "Some domains have no information. If they are not key domains, record domain 'overall' "
                f"as one of: {', '.join(allowed[1:])}."
            )
        else:
            rule, allowed = worst, [worst]

    if recorded is None:
        return rule, rule, notes
    if recorded in allowed:
        return recorded, rule, notes
    notes.append(
        f"The recorded overall '{recorded}' is not allowed by the {tool} rule for these domain judgements "
        f"(allowed: {', '.join(allowed)}); the rule result '{rule}' is used."
    )
    return rule, rule, notes


def rob_overview(conn: sqlite3.Connection) -> dict[str, Any]:
    """Per-paper RoB results, traffic-light rows and counts (no 'ok' key, lists not capped)."""
    rows = conn.execute(
        "SELECT paper_ref, tool, domain, judgement, quote, quote_verified FROM rob ORDER BY paper_ref, domain"
    ).fetchall()
    by_paper: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_paper[r["paper_ref"]].append(r)

    papers: list[dict[str, Any]] = []
    table: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    unverified: list[str] = []
    for paper_ref, p_rows in by_paper.items():
        tool = p_rows[0]["tool"]
        judgements = {r["domain"]: r["judgement"] for r in p_rows if r["domain"] != "overall"}
        recorded = next((r["judgement"] for r in p_rows if r["domain"] == "overall"), None)
        if any(not r["quote_verified"] and (r["quote"] or "").strip() for r in p_rows) or any(
            not (r["quote"] or "").strip() and _quote_required(tool, r["domain"], r["judgement"]) for r in p_rows
        ):
            unverified.append(paper_ref)
        overall, rule, notes = _overall(tool, judgements, recorded)
        summary: dict[str, Any] = {
            "paper_ref": paper_ref,
            "tool": tool,
            "domains": judgements,
            "missing_domains": [d for d in ROB_TOOLS[tool] if d not in judgements],
            "overall": overall,
            "overall_by_rule": rule,
            "overall_recorded": recorded,
            "notes": notes,
        }
        if tool == "robins_i":
            summary["no_information_domains"] = [d for d, j in judgements.items() if j == "no_information"]
        if tool in NOS_STAR_RANGES:
            summary["total_stars"] = sum(int(j) for j in judgements.values())
        papers.append(summary)
        row = {"paper_ref": paper_ref, "tool": tool}
        row.update({d: judgements.get(d) for d in ROB_TOOLS[tool]})
        row["overall"] = overall
        table.append(row)
        counts[overall] = counts.get(overall, 0) + 1
    return {"papers": papers, "table": table, "counts_by_overall": counts, "papers_with_unverified_quotes": unverified}


def _quote_required(tool: str, domain: str, judgement: str) -> bool:
    if domain == "overall":
        return False
    if tool == "robins_i" and judgement == "no_information":
        return False
    return not (tool in NOS_STAR_RANGES and judgement == "0")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@safe_tool
def claim_record(
    review_id: ReviewId,
    claim_text: Annotated[str, Field(description="The claim as written in the report (max 2000 chars).")],
    paper_ref: PaperRef,
    label: Annotated[str, Field(description="Your judgement of how well this paper supports the claim: 'supported', 'partial' or 'unsupported'.")],
    reason: Annotated[str, Field(description="Why you chose the label, in one or two sentences (max 1000 chars).")],
    source_text: SourceText = "",
    quote: Annotated[
        str,
        Field(description="Verbatim passage from source_text that supports the claim (max 2000 chars). Required for 'supported' and 'partial'. Use '...' to join separate parts."),
    ] = "",
) -> dict[str, Any]:
    """Record your judgement of whether one paper supports one claim, and check the quote you rely on.

    Call once per (claim, paper) pair after reading the paper. 'supported' and
    'partial' need a quote and the source_text it comes from; the server checks the
    quote appears in that text (case, whitespace, curly quotes, dashes and line-break
    hyphens are ignored; at least 12 characters; matched on word boundaries) and stores
    quote_verified. A claim recorded again for the same paper (same normalised text)
    replaces the earlier record.

    Returns: claim_id, updated, quote_verified, quote_note (why the quote did not
    verify), warnings.

    Does NOT judge support itself: the label is yours, the server only checks the quote.
    """
    label_clean = choice_arg(label, "label", CLAIM_LABELS)
    claim = text_arg(claim_text, "claim_text", max_len=2000)
    ref = paper_ref_arg(paper_ref)
    reason_clean = text_arg(reason, "reason", max_len=1000)
    source = size_arg(source_text, "source_text", max_len=MAX_SOURCE_CHARS)
    quote_clean = text_arg(quote, "quote", required=False, max_len=2000) or ""
    if label_clean in ("supported", "partial"):
        if not quote_clean:
            fail(f"quote is required for label '{label_clean}': copy the supporting passage from source_text")
        if not source.strip():
            fail(f"source_text is required for label '{label_clean}' so the quote can be checked")

    warnings: list[str] = []
    quote_verified, quote_note = False, None
    if quote_clean:
        quote_verified, quote_note = check_quote(quote_clean, source)
        if not quote_verified and label_clean in ("supported", "partial"):
            warnings.append(
                "Quote was not found in the supplied source text. The claim is listed as suspicious by "
                "claim_report until you record a quote that verifies."
            )

    claim_key = hashlib.sha256(normalize_text(claim).encode("utf-8")).hexdigest()
    chars, digest = _source_fingerprint(source)
    with store.review_db(review_id, write=True) as conn:
        existing = conn.execute(
            "SELECT id, source_sha256, source_chars FROM claims WHERE claim_key = ? AND paper_ref = ?", (claim_key, ref)
        ).fetchone()
        fb_sha = existing["source_sha256"] if existing else None
        fb_len = existing["source_chars"] if existing else None

        values = (claim, label_clean, reason_clean, quote_clean, int(quote_verified), chars, digest, store.now_iso())
        if existing:
            conn.execute(
                """UPDATE claims SET claim_text = ?, label = ?, reason = ?, quote = ?, quote_verified = ?,
                       source_chars = ?, source_sha256 = ?, updated_at = ? WHERE id = ?""",
                values + (existing["id"],),
            )
            claim_id = existing["id"]
        else:
            claim_id = conn.execute(
                """INSERT INTO claims (claim_text, label, reason, quote, quote_verified, source_chars,
                       source_sha256, updated_at, claim_key, paper_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                values + (claim_key, ref),
            ).lastrowid
        source_changed = (
            source_tracking.record_observation(
                conn,
                ref,
                "claim_record",
                claim_key,
                source,
                fallback_sha256=fb_sha,
                fallback_length=fb_len,
            )
            if source
            else None
        )

    if source_changed:
        warnings.append(source_tracking.format_change_warning(source_changed))

    res = {
        "ok": True,
        "claim_id": claim_id,
        "updated": existing is not None,
        "quote_verified": bool(quote_verified),
        "quote_note": quote_note,
        "warnings": warnings,
    }
    if source_changed:
        res["source_changed"] = source_changed
    return res


@safe_tool
def claim_report(
    review_id: ReviewId,
    report_text: Annotated[
        str | None,
        Field(description="Optional final report text. If given, weak claims whose text still appears in it are listed."),
    ] = None,
) -> dict[str, Any]:
    """Summarise the recorded claim judgements and list the ones that need attention.

    Returns: total, counts_by_label, quotes_not_verified (claims with a quote that
    was not found in its source text), suspicious_count and suspicious ('supported'
    or 'partial' claims whose quote did not verify), unsupported_count and
    unsupported, weak_claims_in_report (suspicious or unsupported claims whose text
    appears verbatim in report_text; null when no report_text), truncated (a list
    was cut at 200), method, next_step.

    The labels were assigned by the calling agent; the server checked only that each
    quote appears in the supplied source text. This is not independent verification.
    """
    text = size_arg(report_text, "report_text") if report_text is not None else None
    with store.review_db(review_id) as conn:
        summary = claims_summary(conn, text)
    if summary["weak_claims_in_report"]:
        step = "Remove or rewrite the claims in weak_claims_in_report, or record a verified quote for them."
    elif summary["suspicious_count"]:
        step = "Re-check the suspicious claims: record a quote copied verbatim from the source text, or relabel them."
    elif summary["total"] == 0:
        step = "No claims recorded yet: record each key claim of the report with claim_record."
    else:
        step = "No suspicious claims."
    return {
        "ok": True,
        **summary,
        "method": (
            "Labels were assigned by the calling agent. The server checked only that "
            "each quote appears in the supplied source text. This is not independent verification."
        ),
        "next_step": step,
    }


@safe_tool
def rob_record(
    review_id: ReviewId,
    paper_ref: PaperRef,
    tool: Annotated[
        str,
        Field(description="'rob2' (randomised trials), 'robins_i' (non-randomised studies of interventions), 'nos' (Newcastle-Ottawa, cohort) or 'nos_case_control'."),
    ],
    domain: Annotated[
        str,
        Field(
            description=(
                "rob2: randomisation_process, deviations_from_intended_interventions, missing_outcome_data, "
                "measurement_of_the_outcome, selection_of_the_reported_result (or D1-D5). robins_i: confounding, "
                "selection_of_participants, classification_of_interventions, deviations_from_intended_interventions, "
                "missing_data, measurement_of_outcomes, selection_of_the_reported_result (or D1-D7). nos: selection, "
                "comparability, outcome. nos_case_control: selection, comparability, exposure. rob2 and robins_i "
                "also accept 'overall' for your own overall judgement."
            )
        ),
    ],
    judgement: Annotated[
        str | int,
        Field(
            description=(
                "rob2: low, some_concerns, high. robins_i: low, moderate, serious, critical, no_information. "
                "nos: star count (selection 0-4, comparability 0-2, outcome or exposure 0-3)."
            )
        ),
    ],
    quote: Annotated[
        str,
        Field(description="Verbatim passage from source_text supporting the judgement. Required except for 'overall', robins_i no_information and 0 stars."),
    ] = "",
    source_text: SourceText = "",
    rationale: Annotated[
        str | None,
        Field(description="Your reasoning (max 1000 chars). Required for domain 'overall'."),
    ] = None,
) -> dict[str, Any]:
    """Record one risk-of-bias domain judgement for one paper and check its supporting quote.

    Call once per domain. Domains and judgements are fixed per tool and anything
    else is refused; case, spaces and hyphens are forgiven ('Some concerns').
    A paper is assessed with one tool only. Re-sending the same paper + domain
    replaces the judgement. rob_summary computes the overall judgement.

    Returns: updated, tool, domain, judgement (canonical), quote_verified,
    quote_note, domains_recorded, domains_missing (for this paper), warnings.

    Does NOT assess bias itself: the judgement is yours.
    """
    ref = paper_ref_arg(paper_ref)
    tool_clean = choice_arg(tool, "tool", tuple(ROB_TOOLS), {"rob_2": "rob2", "robins": "robins_i", "newcastle_ottawa": "nos"})
    domains = ROB_TOOLS[tool_clean]
    overall_ok = tool_clean in SEVERITY
    allowed_domains = domains + (("overall",) if overall_ok else ())
    try:
        domain_clean = choice_arg(domain, "domain", allowed_domains, DOMAIN_ALIASES.get(tool_clean))
    except store.StoreError:
        fail(f"domain '{domain}' is invalid for tool '{tool_clean}'. Allowed domains: {', '.join(allowed_domains)}")
    if tool_clean in NOS_STAR_RANGES:
        lo, hi = NOS_STAR_RANGES[tool_clean][domain_clean]
        allowed = tuple(str(i) for i in range(lo, hi + 1))
        judgement_clean = str(judgement).strip()
        if isinstance(judgement, bool) or judgement_clean not in allowed:
            fail(
                f"judgement '{judgement}' is invalid for NOS domain '{domain_clean}'. "
                f"Allowed star counts: {', '.join(allowed)}"
            )
    else:
        allowed = ROB2_JUDGEMENTS if tool_clean == "rob2" else ROBINS_I_JUDGEMENTS
        try:
            judgement_clean = choice_arg(judgement, "judgement", allowed, JUDGEMENT_ALIASES)
        except store.StoreError:
            fail(
                f"judgement '{judgement}' is invalid for tool '{tool_clean}'. Allowed judgements: {', '.join(allowed)}"
            )
    quote_clean = text_arg(quote, "quote", required=False, max_len=2000) or ""
    source = size_arg(source_text, "source_text", max_len=MAX_SOURCE_CHARS)
    rationale_clean = text_arg(rationale, "rationale", required=False, max_len=1000)
    if domain_clean == "overall" and not rationale_clean:
        fail("rationale is required for domain 'overall': say why the overall judgement differs from the domains")
    if _quote_required(tool_clean, domain_clean, judgement_clean) and not quote_clean:
        fail(f"quote is required for tool '{tool_clean}' with judgement '{judgement_clean}'")

    warnings: list[str] = []
    quote_verified, quote_note = False, None
    if quote_clean:
        quote_verified, quote_note = check_quote(quote_clean, source)
        if not quote_verified:
            warnings.append("Quote was not found in the supplied source text.")
    chars, digest = _source_fingerprint(source)

    with store.review_db(review_id, write=True) as conn:
        other = conn.execute(
            "SELECT DISTINCT tool FROM rob WHERE paper_ref = ? AND tool != ?", (ref, tool_clean)
        ).fetchone()
        if other:
            fail(f"paper '{ref}' already has assessments using tool '{other[0]}'; cannot mix tools")
        existing = conn.execute(
            "SELECT source_sha256, source_chars FROM rob WHERE paper_ref = ? AND tool = ? AND domain = ?",
            (ref, tool_clean, domain_clean),
        ).fetchone()
        fb_sha = existing["source_sha256"] if existing else None
        fb_len = existing["source_chars"] if existing else None

        conn.execute(
            """INSERT INTO rob (paper_ref, tool, domain, judgement, quote, quote_verified, source_chars,
                   source_sha256, updated_at, rationale) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(paper_ref, tool, domain) DO UPDATE SET judgement = excluded.judgement,
                   quote = excluded.quote, quote_verified = excluded.quote_verified,
                   source_chars = excluded.source_chars, source_sha256 = excluded.source_sha256,
                   updated_at = excluded.updated_at, rationale = excluded.rationale""",
            (ref, tool_clean, domain_clean, judgement_clean, quote_clean, int(quote_verified), chars, digest,
             store.now_iso(), rationale_clean),
        )
        recorded = {r[0] for r in conn.execute("SELECT domain FROM rob WHERE paper_ref = ?", (ref,))}
        source_changed = (
            source_tracking.record_observation(
                conn,
                ref,
                "rob_record",
                domain_clean,
                source,
                fallback_sha256=fb_sha,
                fallback_length=fb_len,
            )
            if source
            else None
        )

    if source_changed:
        warnings.append(source_tracking.format_change_warning(source_changed))

    res = {
        "ok": True,
        "updated": existing is not None,
        "paper_ref": ref,
        "tool": tool_clean,
        "domain": domain_clean,
        "judgement": judgement_clean,
        "quote_verified": bool(quote_verified),
        "quote_note": quote_note,
        "domains_recorded": len(recorded & set(domains)),
        "domains_missing": [d for d in domains if d not in recorded],
        "warnings": warnings,
    }
    if source_changed:
        res["source_changed"] = source_changed
    return res


@safe_tool
def rob_summary(review_id: ReviewId) -> dict[str, Any]:
    """Overall risk-of-bias judgement per paper by the tool's published rule, plus traffic-light table rows.

    Rules: RoB 2 (Sterne et al. 2019), ROBINS-I (Sterne et al. 2016) and the AHRQ
    thresholds for Newcastle-Ottawa stars; see overall_rule in the result. Where the
    guidance leaves room for reviewer judgement (RoB 2 with several 'some concerns'
    domains, ROBINS-I with 'no information' domains) the result says so in notes, and
    an 'overall' judgement you recorded with rob_record is used when the rule allows it.

    Returns: papers (paper_ref, tool, domains, missing_domains, overall,
    overall_by_rule, overall_recorded, notes, and total_stars for NOS or
    no_information_domains for ROBINS-I), table (one row per paper with each domain
    and overall, for a traffic-light plot), counts_by_overall,
    papers_with_unverified_quotes, overall_rule, truncated (lists cut at 200).

    Does NOT assess bias; it applies fixed rules to your recorded judgements.
    """
    with store.review_db(review_id) as conn:
        overview = rob_overview(conn)
    papers, cut_a = cap(overview["papers"])
    table, _ = cap(overview["table"])
    unverified, cut_b = cap(overview["papers_with_unverified_quotes"])
    return {
        "ok": True,
        "papers": papers,
        "table": table,
        "counts_by_overall": overview["counts_by_overall"],
        "papers_with_unverified_quotes": unverified,
        "overall_rule": OVERALL_RULES,
        "truncated": cut_a or cut_b,
    }


def register(server: MCPServer) -> None:
    for tool in (claim_record, claim_report, rob_record, rob_summary):
        server.tool()(tool)

