# SPDX-License-Identifier: MIT
"""Deterministic duplicate detection over exported search records (used by dedup_papers).

Pure functions: no database, no network.
"""

import re
import unicodedata
from typing import Any

from citeproof_mcp import store

METHOD = "dedup_papers: exact DOI, then PMID, then normalised title + year + first-author surname"


def _ascii_fold(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def _surname(record: dict[str, Any]) -> str:
    raw = record.get("first_author")
    if not raw:
        authors = record.get("authors")
        if isinstance(authors, (list, tuple)) and authors:
            raw = authors[0]
        elif isinstance(authors, str):
            raw = re.split(r";| and ", authors)[0]
    if not isinstance(raw, str) or not raw.strip():
        return ""
    raw = _ascii_fold(raw.strip())
    if "," in raw:
        name = raw.split(",")[0]
    else:
        tokens = raw.split()
        # 'Smith J' / 'Smith JA' (Vancouver) puts the surname first.
        name = tokens[0] if len(tokens) > 1 and re.fullmatch(r"[A-Z]{1,3}\.?", tokens[-1]) else tokens[-1]
    return re.sub(r"[^a-z]", "", name.lower())


def title_key(title: Any) -> str:
    return store.normalize_title(title) if isinstance(title, str) else ""


def _year(value: Any) -> str:
    match = re.search(r"(1[89]|20|21)\d{2}", str(value)) if value is not None else None
    return match.group(0) if match else ""


def find_duplicates(papers: list[dict[str, Any]]) -> dict[str, Any]:
    """Cluster records that are the same paper. Deterministic and conservative.

    Records join a cluster when they share a normalised DOI, a PMID, or the same
    normalised title (at least 20 characters) with the same year and first-author
    surname. Records whose titles match but whose year or first author differ are
    not merged; they are reported in ``possible_duplicates`` for the agent to decide.
    """
    parent = list(range(len(papers)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    matched_on: dict[int, set] = {}

    def union(i: int, j: int, how: str) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)
        matched_on.setdefault(i, set()).add(how)
        matched_on.setdefault(j, set()).add(how)

    first_seen: dict[tuple[str, str], int] = {}
    titles: dict[str, list[int]] = {}
    for i, p in enumerate(papers):
        doi = store.normalize_doi(p.get("doi")) if p.get("doi") else None
        pmid = str(p.get("pmid")).strip() if p.get("pmid") is not None else ""
        pmid = pmid[5:].strip() if pmid.lower().startswith("pmid:") else pmid
        title = title_key(p.get("title"))
        keys = []
        if doi:
            keys.append(("doi", doi))
        if pmid.isdigit():
            keys.append(("pmid", str(int(pmid))))
        if len(title) >= 20:
            titles.setdefault(title, []).append(i)
            surname, year = _surname(p), _year(p.get("year"))
            if surname and year:
                keys.append(("title_year_author", f"{title}|{year}|{surname}"))
        for key in keys:
            if key in first_seen:
                union(first_seen[key], i, key[0])
            else:
                first_seen[key] = i

    clusters: dict[int, list[int]] = {}
    for i in range(len(papers)):
        clusters.setdefault(find(i), []).append(i)

    def completeness(i: int) -> tuple[int, int]:
        p = papers[i]
        score = sum(1 for k in ("doi", "pmid", "title", "year", "abstract") if p.get(k))
        score += 1 if (p.get("authors") or p.get("first_author")) else 0
        return (-score, i)

    out_clusters: list[dict[str, Any]] = []
    removed: list[int] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        kept = min(members, key=completeness)
        dois = {store.normalize_doi(papers[i].get("doi")) for i in members if papers[i].get("doi")}
        dois.discard(None)
        removed.extend(i for i in members if i != kept)
        out_clusters.append(
            {
                "kept_index": kept,
                "kept_id": papers[kept].get("id"),
                "member_indices": members,
                "matched_on": sorted(set().union(*(matched_on.get(i, set()) for i in members))),
                "conflicting_dois": len(dois) > 1,
            }
        )
    out_clusters.sort(key=lambda c: c["kept_index"])

    possible: list[dict[str, Any]] = []
    for title, idxs in titles.items():
        roots = sorted({find(i) for i in idxs})
        if len(roots) > 1:
            possible.append({"indices": sorted(idxs), "title": title[:120], "reason": "same title, different year or first author (not merged)"})
    possible.sort(key=lambda p: p["indices"][0])
    return {"clusters": out_clusters, "removed_indices": sorted(removed), "possible_duplicates": possible}
