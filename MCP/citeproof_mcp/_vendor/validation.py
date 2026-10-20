# SPDX-License-Identifier: MIT
"""Phase 5 Deterministic Citation & Claim Validation Pipeline for Research Mode.

Enforces resolution gates (citations and claim chains resolve to located sources),
numeric metric matching, and PRISMA consistency.

Scope: none of these gates tests that a cited passage supports the sentence it is
attached to. "verified" in names such as verified_citations means "resolved against
the corpus" only, and ReviewClaim.validation_status never reports "verified".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from citeproof_mcp._vendor.evidence import (
    PRISMATracker,
    ValidationReport,
    make_paper_id,
)
from citeproof_mcp._vendor.prose_lint import lint_prose

try:
    from citeproof_mcp._vendor.doi_verify import extract_dois  # type: ignore[import-untyped]
except ImportError:
    extract_dois = None

logger = logging.getLogger(__name__)

# Regular expressions for identifying in-text APA citations and DOIs
CITATION_PATTERN = re.compile(
    # Any Unicode letter, so surnames such as Müller or Núñez are recognised.
    r"\(((?:[^\W\d_]|[\s\-&.,'])+?),\s*(19\d\d|20\d\d)\)"
)

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[^\s\"'<>\])]+")

# Same marker grammar as writing.EV_MARKER_RE.
EV_CITE_RE = re.compile(r"\[\s*ev:\s*([A-Za-z0-9_.\-]+?)\s*\]", re.IGNORECASE)

METRIC_KEYWORD_PATTERN = re.compile(
    r"(?:\b\d+\.?\d*|\d+)\s*(?:%|percent\b|BLEU\b|F1\b|accuracy\b|points\b|ms\b|seconds\b|x\b|fold\b)",
    re.IGNORECASE
)


def extract_numerical_claims(text: str) -> list[str]:
    """Extract individual sentences containing quantitative metrics or numerical performance claims."""
    if not text:
        return []
    sentences = re.split(r"(?<=[.?!])\s+", text)
    return [s.strip() for s in sentences if METRIC_KEYWORD_PATTERN.search(s)]


def validate_claim_chains(
    review_claims: list[dict[str, Any]],
    evidence_records: list[dict[str, Any]],
    paper_records: list[dict[str, Any]],
) -> tuple[int, int, list[str], list[str]]:
    """Validate structured claim_id -> evidence_id -> paper_id -> source_url/doi chains.

    Every rendered ReviewClaim must carry at least one supporting_evidence_id
    that resolves to a known EvidenceRecord, whose paper_id resolves to a known
    PaperRecord carrying a source_url or DOI locator. Returns
    (total, resolved, unresolved_claim_ids, human-readable failure details).
    """
    if not review_claims:
        return 0, 0, [], []

    evidence_by_id = {
        e.get("evidence_id"): e for e in evidence_records if e.get("evidence_id")
    }
    papers_by_id = {
        p.get("paper_id"): p for p in paper_records if p.get("paper_id")
    }

    total = len(review_claims)
    unresolved: list[str] = []
    details: list[str] = []

    for c in review_claims:
        cid = str(c.get("claim_id") or "<unnamed claim>")
        supporting = c.get("supporting_evidence_ids") or []
        if not supporting:
            unresolved.append(cid)
            details.append(f"{cid}: no supporting evidence (empty supporting_evidence_ids).")
            continue

        broken = False
        for eid in supporting:
            ev = evidence_by_id.get(eid)
            if ev is None:
                broken = True
                details.append(f"{cid}: supporting evidence '{eid}' does not resolve to any EvidenceRecord.")
                continue
            pid = ev.get("paper_id")
            paper = papers_by_id.get(pid)
            if paper is None:
                broken = True
                details.append(f"{cid}: evidence '{eid}' points at unknown paper_id '{pid}'.")
                continue
            if not (paper.get("source_url") or paper.get("doi")):
                broken = True
                details.append(
                    f"{cid}: evidence '{eid}' paper '{pid}' lacks a source locator (source_url/doi)."
                )

        if broken:
            unresolved.append(cid)

    resolved = total - len(unresolved)
    return total, resolved, unresolved, details


def _record_get(rec: Any, key: str, default: Any = None) -> Any:
    """Safely get a field from a dictionary or Pydantic model."""
    if isinstance(rec, dict):
        return rec.get(key, default)
    return getattr(rec, key, default)


def _normalize_doi(doi: str | None) -> str:
    """Normalize DOI: lowercase, strip prefixes and trailing punctuation."""
    if not doi:
        return ""
    d = str(doi).strip().lower()
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
    ):
        if d.startswith(prefix):
            d = d[len(prefix):].strip()
            break
    return d.rstrip(".,;:!?)>]'\"")


def _extract_first_author_surname(authors: list[Any] | None) -> str | None:
    """Extract the first author's surname using the last token before a comma rule."""
    if not authors:
        return None
    for a in authors:
        if not a or not str(a).strip():
            continue
        tokens = str(a).split(",")[0].strip().split()
        if tokens:
            surname = tokens[-1].lower().strip()
            if len(surname) > 1:
                return surname
    return None


def _extract_title_words(title: str | None) -> list[str]:
    """Lowercase and remove punctuation, returning word tokens."""
    clean = re.sub(r"[^\w\s]", " ", (title or "").lower())
    return [w for w in clean.split() if w]


def _title_prefix(title: str | None, max_words: int = 4) -> str:
    """Generate title prefix for authorless papers (first 4 significant words)."""
    words = _extract_title_words(title)
    return " ".join(words[:max_words])


@dataclass
class _CitationIndex:
    doi_to_paper_ids: dict[str, list[str]] = field(default_factory=dict)
    author_year_to_paper_ids: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    title_year_to_paper_ids: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    min_known_surname_len: int = 0
    paper_ids: list[str] = field(default_factory=list)


def _build_citation_index(paper_records: list[Any]) -> _CitationIndex:
    """Build citation lookup indexes from paper records."""
    index = _CitationIndex()

    for p in paper_records:
        raw_doi = _record_get(p, "doi")
        title = str(_record_get(p, "title") or "")
        year = str(_record_get(p, "year", "n.d."))

        pid = _record_get(p, "paper_id")
        if not pid:
            pid = make_paper_id(raw_doi, title, year)
        pid = str(pid)
        if pid not in index.paper_ids:
            index.paper_ids.append(pid)

        # Index by normalized DOI
        norm_doi = _normalize_doi(raw_doi)
        if norm_doi:
            pids = index.doi_to_paper_ids.setdefault(norm_doi, [])
            if pid not in pids:
                pids.append(pid)

        # Index by author or title prefix
        authors = _record_get(p, "authors") or []
        first_surname = _extract_first_author_surname(authors)
        if first_surname:
            pids = index.author_year_to_paper_ids.setdefault((first_surname, year), [])
            if pid not in pids:
                pids.append(pid)
        else:
            # Authorless paper: index by title prefix (first 4 words)
            t_prefix = _title_prefix(title, max_words=4)
            if t_prefix:
                pids = index.title_year_to_paper_ids.setdefault((t_prefix, year), [])
                if pid not in pids:
                    pids.append(pid)

    if index.author_year_to_paper_ids:
        index.min_known_surname_len = min(len(k[0]) for k in index.author_year_to_paper_ids)
    else:
        index.min_known_surname_len = 0

    return index


@dataclass
class CitationResolution:
    total: int = 0
    verified: int = 0
    unverified: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    ambiguous_citations: list[str] = field(default_factory=list)
    matched_paper_ids: set[str] = field(default_factory=set)
    doi_citations_checked: int = 0


def resolve_citations(
    text: str,
    paper_records: list[Any],
    evidence_records: list[Any] | None = None,
    review_claims: list[Any] | None = None,
) -> CitationResolution:
    """Resolve inline citations (DOIs, author-year, and title fallback) against the corpus."""
    if not text:
        return CitationResolution()

    index = _build_citation_index(paper_records)

    total = 0
    verified = 0
    unverified: list[str] = []
    ambiguous_citations: list[str] = []
    matched_paper_ids: set[str] = set()
    doi_citations_checked = 0

    # 1. Resolve DOIs in prose
    raw_dois: list[str] = []
    if extract_dois is not None:
        try:
            raw_dois = extract_dois(text) or []
        except Exception:  # noqa: BLE001 - fall back to the plain DOI pattern
            raw_dois = DOI_PATTERN.findall(text)
    else:
        raw_dois = DOI_PATTERN.findall(text)

    for raw_doi in raw_dois:
        norm_doi = _normalize_doi(raw_doi)
        if not norm_doi:
            continue
        doi_citations_checked += 1
        total += 1
        if norm_doi in index.doi_to_paper_ids:
            verified += 1
            matched_paper_ids.update(index.doi_to_paper_ids[norm_doi])
        else:
            unverified.append(f"doi:{norm_doi}")

    # 2. Resolve Author-year (and title-prefix fallback) citations
    matches = CITATION_PATTERN.findall(text)
    for author_str, year in matches:
        total += 1

        # Split on "and" only as a standalone word so surnames such as Rand,
        # Chandra, and Hollande survive intact.
        first_author = re.split(r"\band\b", author_str.split("&")[0])[0]
        first_author = first_author.replace("et al.", "").replace("et al", "").strip()
        tokens = first_author.split()

        matching_papers: list[str] = []
        if tokens:
            surname = tokens[-1].lower().strip()
            if (
                len(surname) >= index.min_known_surname_len
                and (surname, year) in index.author_year_to_paper_ids
            ):
                matching_papers = index.author_year_to_paper_ids[(surname, year)]

        if len(matching_papers) == 1:
            verified += 1
            matched_paper_ids.add(matching_papers[0])
        elif len(matching_papers) > 1:
            verified += 1
            ambiguous_citations.append(f"({author_str}, {year})")
            matched_paper_ids.update(matching_papers)
        else:
            # None -> try the title-prefix fallback
            title_words = _extract_title_words(author_str)
            title_prefix = " ".join(title_words[:4])

            matching_title_papers = index.title_year_to_paper_ids.get((title_prefix, year), [])
            if not matching_title_papers and len(title_words) >= 2:
                prefix_str = " ".join(title_words)
                candidates = []
                for (tp, y), pids in index.title_year_to_paper_ids.items():
                    if y == year and (tp == prefix_str or tp.startswith(prefix_str + " ")):
                        candidates.extend(pids)
                if candidates:
                    matching_title_papers = list(dict.fromkeys(candidates))

            if len(matching_title_papers) == 1:
                verified += 1
                matched_paper_ids.add(matching_title_papers[0])
            elif len(matching_title_papers) > 1:
                verified += 1
                ambiguous_citations.append(f"({author_str}, {year})")
                matched_paper_ids.update(matching_title_papers)
            else:
                unverified.append(f"({author_str}, {year})")

    # 3. Detect orphans
    # A paper is an orphan when no citation resolved to it AND none of its evidence ids appear in [EV:<id>]
    # claims_linker_node strips [EV:...] markers from the prose and records them
    # as review-claim supporting_evidence_ids, so both sources count.
    cited_ev_ids = set(EV_CITE_RE.findall(text))
    for claim in review_claims or []:
        cited_ev_ids.update(str(e) for e in (_record_get(claim, "supporting_evidence_ids") or []))
    paper_ev_map: dict[str, set[str]] = {}
    for ev in (evidence_records or []):
        ev_pid = str(_record_get(ev, "paper_id") or "")
        ev_id = str(_record_get(ev, "evidence_id") or "")
        if ev_pid and ev_id:
            paper_ev_map.setdefault(ev_pid, set()).add(ev_id)

    orphans: list[str] = []
    for p in paper_records:
        raw_doi = _record_get(p, "doi")
        title = str(_record_get(p, "title") or "Untitled")
        year = str(_record_get(p, "year", "n.d."))
        pid = _record_get(p, "paper_id")
        if not pid:
            pid = make_paper_id(raw_doi, title, year)
        pid = str(pid)

        citation_resolved = pid in matched_paper_ids

        # Check evidence markers
        ev_ids = paper_ev_map.get(pid, set())
        ev_marker_resolved = bool(ev_ids & cited_ev_ids)
        if not ev_marker_resolved and pid:
            # Fallback when evidence_records not supplied or scoped naming used
            ev_marker_resolved = any(cid.startswith(f"{pid}_") for cid in cited_ev_ids)

        if not citation_resolved and not ev_marker_resolved:
            orphans.append(title)

    return CitationResolution(
        total=total,
        verified=verified,
        unverified=unverified,
        orphans=orphans,
        ambiguous_citations=ambiguous_citations,
        matched_paper_ids=matched_paper_ids,
        doi_citations_checked=doi_citations_checked,
    )


def validate_citations_in_text(
    text: str,
    paper_records: list[dict[str, Any]],
    evidence_records: list[dict[str, Any]] | None = None,
) -> tuple[int, int, list[str], list[str]]:
    """Resolve all inline citations against the PaperRecord store.

    A citation counts as verified when its DOI, (first-author surname, year),
    or (title prefix, year) matches a PaperRecord. Returns
    (total, verified, unverified, orphan_references).
    """
    res = resolve_citations(text, paper_records, evidence_records=evidence_records)
    return res.total, res.verified, res.unverified, res.orphans


async def citation_validator_node(state: dict[str, Any]) -> dict[str, Any]:
    """Validator 1: Resolves inline citations, orphan references, and claim chains.

    Beyond regex-matching rendered author-year text, resolves every structured
    ReviewClaim against the evidence store: each supporting_evidence_id must
    resolve to an EvidenceRecord whose paper_id resolves to a located paper.
    Claims are annotated in the manifest itself: "resolved" when the chain
    resolves, "flagged" when it does not. Resolution only; nothing here tests that
    the cited passage supports the sentence.
    """
    papers = state.get("paper_records") or state.get("screened_papers") or []
    ev_dicts = state.get("evidence_records") or []
    all_prose = " ".join([
        str(state.get("literature_review") or ""),
        str(state.get("results") or ""),
        str(state.get("discussion") or ""),
        str(state.get("introduction") or "")
    ])

    res = resolve_citations(
        all_prose, papers, evidence_records=ev_dicts, review_claims=state.get("review_claims") or []
    )
    total, verified, unverified, _orphans = res.total, res.verified, res.unverified, res.orphans

    review_claims = state.get("review_claims") or []
    total_rc, resolved_rc, unresolved_claims, _details = validate_claim_chains(
        review_claims, ev_dicts, papers
    )
    unresolved_set = set(unresolved_claims)
    adjudicated = [
        dict(c, validation_status="flagged" if str(c.get("claim_id")) in unresolved_set else "resolved")
        for c in review_claims
    ]
    logger.info(
        f"citation_validator: {verified}/{total} citations matched a known paper record; "
        f"{resolved_rc}/{total_rc} review claims resolve full provenance chains."
    )

    return {
        "unverified_citations": unverified,
        "unresolved_claims": unresolved_claims,
        "review_claims": adjudicated,
    }


def _number_matches(claimed: float, expected: float, unit_or_scale: str = "") -> bool:
    """Compare a sentence number against an evidence value with scale tolerance.

    Accepts a direct match within a small relative tolerance, and also accepts
    percentage/fraction restatements (e.g. 0.942 reported as 94.2%) when the
    metric's unit or scale indicates a percentage.
    """
    candidates = [expected]
    unit = (unit_or_scale or "").lower()
    is_pct = "%" in unit or "percent" in unit
    if is_pct or 0 < abs(expected) <= 1:
        candidates.append(expected * 100.0)
    if is_pct or abs(expected) > 1:
        candidates.append(expected / 100.0)

    for cand in candidates:
        tolerance = max(abs(cand) * 0.01, 0.005)
        if abs(claimed - cand) <= tolerance:
            return True
    return False


def _mentions_metric(sentence_lower: str, metric_name: str) -> bool:
    """Check whether a sentence references the evidence metric by name."""
    metric = (metric_name or "").strip().lower()
    if not metric:
        return False
    if metric in sentence_lower:
        return True
    # Fall back to matching the metric's significant word tokens.
    tokens = [t for t in re.findall(r"[a-z0-9]+", metric) if len(t) > 2]
    return bool(tokens) and all(t in sentence_lower for t in tokens)


async def claim_validator_node(state: dict[str, Any]) -> dict[str, Any]:
    """Validator 2: Matches numbers in quantitative sentences and structured claims to EvidenceRecord values.

    Numeric match only. A grounded claim keeps its status (its id is reported in
    grounded_quantitative_claim_ids); an ungrounded quantitative claim becomes "unsupported".
    """
    ev_dicts = state.get("evidence_records") or []
    results_text = str(state.get("results") or "")

    # Metric-scoped expectations: a number is only grounded when the sentence
    # names the metric AND the number matches that metric's reported/baseline value.
    metric_expectations: list[tuple[str, float, str]] = []
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for e in ev_dicts:
        eid = e.get("evidence_id")
        if eid:
            evidence_by_id[eid] = e
        metric_name = str(e.get("metric_name") or "").strip()
        if not metric_name:
            continue
        unit = str(e.get("unit_or_scale") or "")
        for value_key in ("reported_value", "baseline_value"):
            v = e.get(value_key)
            if v is None:
                continue
            try:
                metric_expectations.append((metric_name, float(v), unit))
            except (ValueError, TypeError):
                pass

    numerical_sentences = extract_numerical_claims(results_text)
    unsupported = []
    grounded_count = 0

    for sent in numerical_sentences:
        sent_lower = sent.lower()
        nums = []
        for n in re.findall(r"\b\d+\.?\d*\b", sent):
            try:
                nums.append(float(n))
            except (ValueError, TypeError):
                pass

        is_grounded = any(
            _mentions_metric(sent_lower, metric_name) and _number_matches(n, expected, unit)
            for metric_name, expected, unit in metric_expectations
            for n in nums
        )

        if is_grounded:
            grounded_count += 1
        else:
            unsupported.append(sent.strip())

    # Structured quantitative ReviewClaims are grounded through their
    # supporting_evidence_ids, not through free-text sentence matching.
    review_claims = state.get("review_claims") or []
    grounded_quant_ids: list[str] = []
    adjudicated = []
    for c in review_claims:
        c = dict(c)
        if c.get("is_quantitative"):
            claim_lower = str(c.get("claim_text") or "").lower()
            nums = []
            for n in re.findall(r"\b\d+\.?\d*\b", str(c.get("claim_text") or "")):
                try:
                    nums.append(float(n))
                except (ValueError, TypeError):
                    pass
            grounded = any(
                _mentions_metric(claim_lower, str(ev.get("metric_name") or ""))
                and _number_matches(
                    n,
                    float(value),
                    str(ev.get("unit_or_scale") or ""),
                )
                for eid in (c.get("supporting_evidence_ids") or [])
                for ev in [evidence_by_id.get(eid) or {}]
                for field in ("reported_value", "baseline_value")
                for value in [ev.get(field)]
                if value is not None
                for n in nums
            )
            if grounded:
                # A numeric match does not test that the passage supports the
                # claim, so it is reported in grounded_quant_ids and leaves the
                # status untouched rather than upgrading it.
                grounded_quant_ids.append(str(c.get("claim_id")))
            else:
                unsupported.append(str(c.get("claim_id")))
                c["validation_status"] = "unsupported"
        adjudicated.append(c)

    logger.info(
        f"claim_validator: {grounded_count}/{len(numerical_sentences)} numerical sentences grounded; "
        f"{len(grounded_quant_ids)} structured quantitative claims grounded."
    )
    return {
        "unsupported_numerical_claims": unsupported,
        "grounded_claims_count": grounded_count,
        "grounded_quantitative_claim_ids": grounded_quant_ids,
        "review_claims": adjudicated,
    }


async def integrity_auditor_node(state: dict[str, Any]) -> dict[str, Any]:
    """Validator 3: Aggregates the resolution and numeric-match gates and checks PRISMA invariants.

    passed_all_gates means every gate passed. It is not a finding that the cited
    sources support the prose.

    It also runs the deterministic prose lint over the rendered sections. Those
    findings are advisory: they are reported in `prose_issues` and never reach
    `integrity_flags` or `passed_all_gates`.
    """
    raw_tr = state.get("prisma_tracker") or {}
    valid_keys = set(PRISMATracker.model_fields.keys())
    filtered_tr = {k: v for k, v in raw_tr.items() if k in valid_keys} if isinstance(raw_tr, dict) else {}
    tracker = PRISMATracker(**filtered_tr) if filtered_tr else PRISMATracker()
    prisma_errors = tracker.validate_invariants()

    papers = state.get("paper_records") or state.get("screened_papers") or []
    all_prose = " ".join([
        str(state.get("literature_review") or ""),
        str(state.get("results") or ""),
        str(state.get("discussion") or ""),
        str(state.get("introduction") or "")
    ])

    res = resolve_citations(
        all_prose,
        papers,
        evidence_records=state.get("evidence_records") or [],
        review_claims=state.get("review_claims") or [],
    )
    total_cites = res.total
    verified_cites = res.verified
    unverified_cites = res.unverified
    ambiguous_cites = res.ambiguous_citations
    doi_cites_checked = res.doi_citations_checked
    orphans = res.orphans

    unsupported_claims = state.get("unsupported_numerical_claims") or []
    unresolved_claims = state.get("unresolved_claims") or []

    # Structured provenance gate: every rendered review claim must resolve the
    # full claim -> evidence -> paper -> locator chain against the live corpus.
    review_claims = state.get("review_claims") or []
    total_rc, resolved_rc, chain_unresolved, chain_details = validate_claim_chains(
        review_claims,
        state.get("evidence_records") or [],
        papers,
    )
    chain_unresolved = sorted(set(chain_unresolved) | {str(c) for c in unresolved_claims})

    # Retraction audit. A retracted paper that screening excluded is the pipeline
    # working as intended; one that is still included is an integrity failure.
    retracted_by_id = {
        p.get("paper_id") or p.get("title"): p
        for p in [*(state.get("retracted_papers") or []), *papers]
        if p.get("is_retracted") is True
    }
    retracted = list(retracted_by_id.values())
    retracted_still_included = [p for p in retracted if p.get("screening_status") == "included"]
    retracted_excluded_titles = [
        str(p.get("title") or "").strip()
        for p in retracted
        if p.get("screening_status") != "included" and str(p.get("title") or "").strip()
    ]

    # Calculate claim support statistics (advisory only: not added to flags)
    supported_cnt = 0
    partial_cnt = 0
    unsupported_ids: list[str] = []
    for c in review_claims:
        label = c.get("support_label") if isinstance(c, dict) else getattr(c, "support_label", "unchecked")
        cid = str(c.get("claim_id") if isinstance(c, dict) else getattr(c, "claim_id", ""))
        if label == "supported":
            supported_cnt += 1
        elif label == "partial":
            partial_cnt += 1
        elif label == "unsupported" and cid:
            unsupported_ids.append(cid)
    checked_cnt = supported_cnt + partial_cnt + len(unsupported_ids)

    flags: list[str] = []
    if prisma_errors:
        flags.extend(prisma_errors)
    if unverified_cites:
        flags.append(f"{len(unverified_cites)} unverified inline citations found.")
    if ambiguous_cites:
        flags.append(f"{len(ambiguous_cites)} citations match more than one paper.")
    if unsupported_claims:
        flags.append(f"{len(unsupported_claims)} unsupported numerical claims flagged.")
    if chain_unresolved:
        flags.append(
            f"{len(chain_unresolved)} review claims lack a resolvable "
            "claim->evidence->paper->source chain."
        )
    if retracted_still_included:
        flags.append(f"{len(retracted_still_included)} retracted papers are still included.")

    # Prose lint over the five rendered sections, joined with blank lines so a
    # section that opens with "## " keeps its own line and the heading checks can
    # see it. Sections missing from the state are skipped, not treated as empty
    # text. Findings are advisory and never reach `flags`.
    lint_sections = [
        str(state.get(key) or "")
        for key in ("introduction", "literature_review", "results", "discussion", "conclusion")
    ]
    lint_text = "\n\n".join(s for s in lint_sections if s.strip())
    prose_issues = [{"code": i.code, "note": i.note} for i in lint_prose(lint_text).issues]

    report = ValidationReport(
        total_inline_citations=total_cites,
        verified_citations=verified_cites,
        unverified_citations=unverified_cites,
        ambiguous_citations=ambiguous_cites,
        doi_citations_checked=doi_cites_checked,
        unsupported_quantitative_claims=unsupported_claims,
        orphan_references=orphans[:10],
        total_review_claims=total_rc,
        resolved_review_claims=resolved_rc,
        unresolved_review_claims=chain_unresolved,
        claim_support_checked=checked_cnt,
        claims_supported=supported_cnt,
        claims_partial=partial_cnt,
        claims_unsupported=unsupported_ids,
        retracted_papers_excluded=retracted_excluded_titles,
        integrity_flags=flags + chain_details[:10],
        prose_issues=prose_issues,
        prisma_invariants_valid=len(prisma_errors) == 0,
        passed_all_gates=len(flags) == 0,
    )

    logger.info(
        f"integrity_auditor_node complete. Invariants valid: {report.prisma_invariants_valid}; "
        f"prose lint: {len(prose_issues)} advisory issue(s)."
    )
    return {
        "validation_report": report.model_dump()
    }
