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
import unicodedata
from typing import Any, Dict, List, Set, Tuple

from backend.app.models.evidence import ValidationReport, PRISMATracker, PaperRecord

logger = logging.getLogger(__name__)

# Regular expressions for identifying in-text APA citations
CITATION_PATTERN = re.compile(
    r"\(([^()]+?),\s*((?:19|20)\d{2}[a-z]?)\)",
    re.IGNORECASE,
)

METRIC_KEYWORD_PATTERN = re.compile(
    r"(?:\b\d+\.?\d*|\d+)\s*(?:%|percent\b|BLEU\b|F1\b|accuracy\b|points\b|ms\b|seconds\b|x\b|fold\b)",
    re.IGNORECASE
)


def extract_numerical_claims(text: str) -> List[str]:
    """Extract individual sentences containing quantitative metrics or numerical performance claims."""
    if not text:
        return []
    sentences = re.split(r"(?<=[.?!])\s+", text)
    return [s.strip() for s in sentences if METRIC_KEYWORD_PATTERN.search(s)]


def validate_claim_chains(
    review_claims: List[Dict[str, Any]],
    evidence_records: List[Dict[str, Any]],
    paper_records: List[Dict[str, Any]],
) -> Tuple[int, int, List[str], List[str]]:
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
    unresolved: List[str] = []
    details: List[str] = []

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


def _name_variants(value: str) -> Set[str]:
    """Return comparable surname spellings with diacritics normalized."""
    raw = str(value or "").strip().casefold()
    if not raw:
        return set()
    expanded = raw.translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    variants = set()
    for candidate in (raw, expanded):
        ascii_name = "".join(
            ch for ch in unicodedata.normalize("NFKD", candidate)
            if not unicodedata.combining(ch)
        )
        normalized = re.sub(r"[^a-z0-9]+", " ", ascii_name).strip()
        if normalized:
            variants.add(normalized)
    return variants


def _author_surname(author: str) -> str:
    """Extract a family name while retaining common surname particles."""
    value = str(author or "").strip()
    if not value:
        return ""
    if "," in value:
        return value.split(",", 1)[0].strip()

    tokens = value.split()
    if not tokens:
        return ""
    particles = {"da", "de", "del", "della", "di", "dos", "du", "la", "le", "van", "von"}
    start = len(tokens) - 1
    while start > 0 and tokens[start - 1].casefold().strip(".") in particles:
        start -= 1
    return " ".join(tokens[start:])


def _citation_authors(author_str: str) -> List[str]:
    """Extract the author surnames explicitly present in an APA citation."""
    cleaned = re.sub(r"\bet\s+al\.?", "", author_str, flags=re.IGNORECASE).strip(" ,")
    parts = re.split(r"\s*(?:&|\band\b)\s*", cleaned, maxsplit=1, flags=re.IGNORECASE)
    return [_author_surname(part) for part in parts if _author_surname(part)]


def _year_parts(value: Any) -> Tuple[str, str]:
    match = re.search(r"((?:19|20)\d{2})([a-z]?)", str(value or ""), re.IGNORECASE)
    if not match:
        return "", ""
    return match.group(1), match.group(2).lower()


def validate_citations_in_text(
    text: str,
    paper_records: List[Dict[str, Any]]
) -> Tuple[int, int, List[str], List[str]]:
    """Resolve inline author-year citations without accepting ambiguous matches."""
    if not text:
        return 0, 0, [], []

    records = []
    for p in paper_records:
        authors = [str(a) for a in (p.get("authors") or []) if str(a).strip()]
        if not authors:
            continue
        base_year, suffix = _year_parts(p.get("year"))
        if not base_year:
            continue
        records.append({
            "paper": p,
            "year": base_year,
            "suffix": suffix,
            "first": _name_variants(_author_surname(authors[0])),
            "second": _name_variants(_author_surname(authors[1])) if len(authors) > 1 else set(),
            "title": re.sub(r"\W+", " ", str(p.get("title") or "").casefold()).strip(),
        })

    matches = CITATION_PATTERN.findall(text)
    verified = 0
    unverified: List[str] = []
    cited_surnames: Set[str] = set()

    for author_str, cited_year in matches:
        cited_authors = _citation_authors(author_str)
        base_year, suffix = _year_parts(cited_year)
        citation_label = f"({author_str}, {cited_year})"
        if not cited_authors or not base_year:
            unverified.append(citation_label)
            continue

        first_variants = _name_variants(cited_authors[0])
        cited_surnames.update(first_variants)
        candidates = [
            r for r in records
            if r["year"] == base_year and first_variants.intersection(r["first"])
        ]

        if len(cited_authors) > 1:
            second_variants = _name_variants(cited_authors[1])
            candidates = [
                r for r in candidates
                if second_variants.intersection(r["second"])
            ]

        if suffix:
            exact = [r for r in candidates if r["suffix"] == suffix]
            if exact:
                candidates = exact
            else:
                unsuffixed = [r for r in candidates if not r["suffix"]]
                ordered = sorted(unsuffixed, key=lambda r: r["title"])
                index = ord(suffix) - ord("a")
                candidates = [ordered[index]] if 0 <= index < len(ordered) else []

        if len(candidates) == 1:
            verified += 1
        else:
            unverified.append(citation_label)

    orphan_references = []
    for p in paper_records:
        authors = p.get("authors") or []
        if not authors:
            continue
        first_variants = _name_variants(_author_surname(str(authors[0])))
        if first_variants and not first_variants.intersection(cited_surnames):
            orphan_references.append(p.get("title", "Untitled"))

    return len(matches), verified, unverified, orphan_references


async def citation_validator_node(state: Dict[str, Any]) -> Dict[str, Any]:
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

    total, verified, unverified, orphans = validate_citations_in_text(all_prose, papers)

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


async def claim_validator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Validator 2: Matches numbers in quantitative sentences and structured claims to EvidenceRecord values.

    Numeric match only. A grounded claim keeps its status (its id is reported in
    grounded_quantitative_claim_ids); an ungrounded quantitative claim becomes "unsupported".
    """
    ev_dicts = state.get("evidence_records") or []
    results_text = str(state.get("results") or "")

    # Metric-scoped expectations: a number is only grounded when the sentence
    # names the metric AND the number matches that metric's reported/baseline value.
    metric_expectations: List[Tuple[str, float, str]] = []
    evidence_by_id: Dict[str, Dict[str, Any]] = {}
    for e in ev_dicts:
        eid = e.get("evidence_id")
        if eid:
            evidence_by_id[eid] = e
        metric_name = str(e.get("metric_name") or "").strip()
        if not metric_name:
            continue
        unit = str(e.get("unit_or_scale") or "")
        for field in ("reported_value", "baseline_value"):
            v = e.get(field)
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
    grounded_quant_ids: List[str] = []
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


async def integrity_auditor_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Validator 3: Aggregates the resolution and numeric-match gates and checks PRISMA invariants.

    passed_all_gates means every gate passed. It is not a finding that the cited
    sources support the prose.
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

    total_cites, verified_cites, unverified_cites, orphans = validate_citations_in_text(all_prose, papers)
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
    chain_unresolved = sorted(set(chain_unresolved) | set(str(c) for c in unresolved_claims))

    flags: List[str] = []
    if prisma_errors:
        flags.extend(prisma_errors)
    if unverified_cites:
        flags.append(f"{len(unverified_cites)} unverified inline citations found.")
    if unsupported_claims:
        flags.append(f"{len(unsupported_claims)} unsupported numerical claims flagged.")
    if chain_unresolved:
        flags.append(
            f"{len(chain_unresolved)} review claims lack a resolvable "
            "claim->evidence->paper->source chain."
        )

    report = ValidationReport(
        total_inline_citations=total_cites,
        verified_citations=verified_cites,
        unverified_citations=unverified_cites,
        unsupported_claims=unsupported_claims,
        orphan_references=orphans[:10],
        total_review_claims=total_rc,
        resolved_review_claims=resolved_rc,
        unresolved_review_claims=chain_unresolved,
        integrity_flags=flags + chain_details[:10],
        prisma_invariants_valid=len(prisma_errors) == 0,
        passed_all_gates=len(flags) == 0
    )

    logger.info(f"integrity_auditor_node complete. Invariants valid: {report.prisma_invariants_valid}")
    return {
        "validation_report": report.model_dump()
    }
