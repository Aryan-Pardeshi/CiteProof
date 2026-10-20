"""
One-off script: runs 3 real Research Mode pipeline jobs end-to-end and
writes their extracted output to frontend/assets/examples.json for use
on the marketing landing page.

Real LLM + academic-API cost. Each question takes ~15-25 minutes.
Run from the repo root: python scripts/generate_landing_examples.py
"""
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from backend.app.graph.research_mode_builder import (
    get_research_mode_graph,
    set_checkpointer,
)

DB_PATH = repo_root / "data" / "landing_examples.db"
OUTPUT_PATH = repo_root / "frontend" / "assets" / "examples.json"
MAX_CHECKPOINTS = 6  # 3 real checkpoints + safety margin

EXAMPLES: list[dict[str, str]] = [
    {
        "id": "hallucination",
        "topic": "AI hallucination mitigation",
        "question": "What techniques mitigate hallucinations in large language models?",
    },
    {
        "id": "rag-vs-finetuning",
        "topic": "RAG vs fine-tuning",
        "question": "How does retrieval-augmented generation compare to fine-tuning for adapting large language models to domain-specific tasks?",
    },
    {
        "id": "multi-agent-healthcare",
        "topic": "Multi-agent systems in healthcare",
        "question": "How are multi-agent AI systems being applied in healthcare delivery and clinical decision-making?",
    },
]


async def run_to_completion(graph, problem_statement: str) -> dict[str, Any]:
    """Starts a Research Mode run and auto-approves every checkpoint until it completes."""
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    input_val: Any = {
        "thread_id": thread_id,
        "problem_statement": problem_statement,
        "research_objectives": [],
        "research_questions": [],
        "keywords": [],
        "raw_papers": [],
        "screened_papers": [],
        "status": "initializing",
    }

    for iteration in range(MAX_CHECKPOINTS):
        async for event in graph.astream(input_val, config=config):
            print(f"  [{problem_statement[:40]}...] event: {list(event.keys())}", flush=True)

        state = await graph.aget_state(config)
        if not state.next:
            values = state.values
            if values.get("status") != "completed":
                raise RuntimeError(
                    f"Run for {problem_statement!r} stopped with no next step but "
                    f"status={values.get('status')!r}, error={values.get('error')!r}"
                )
            return values

        print(f"  [{problem_statement[:40]}...] paused at checkpoint {iteration + 1}, auto-approving", flush=True)
        input_val = Command(resume={"message": "approve"})

    raise RuntimeError(f"Run for {problem_statement!r} did not complete within {MAX_CHECKPOINTS} checkpoints")


def _source_breakdown(records_by_source: dict[str, int]) -> str:
    parts = [f"{name} ({count})" for name, count in sorted(records_by_source.items(), key=lambda kv: -kv[1])]
    return " · ".join(parts) if parts else "Multiple academic indexes"


def _study_label(paper: dict[str, Any]) -> str:
    authors = paper.get("authors") or []
    year = paper.get("year") or "n.d."
    if not authors:
        return f"Unknown ({year})"
    surname = authors[0].split()[-1] if authors[0].split() else authors[0]
    suffix = " et al." if len(authors) > 1 else ""
    return f"{surname}{suffix} ({year})"


def _evidence_level(record: dict[str, Any]) -> str:
    confidence = record.get("confidence", 0.4)
    # "verified" is the legacy status value from runs saved before the rename to "resolved".
    resolved = record.get("verification_status") in ("resolved", "verified")
    if confidence >= 0.75:
        return "High (Quote anchored)" if resolved else "High"
    if confidence >= 0.5:
        return "Moderate-High"
    return "Moderate"


def extract_example(meta: dict[str, str], values: dict[str, Any]) -> dict[str, Any]:
    prisma = values.get("prisma_tracker") or {}
    records_identified = prisma.get("records_identified", 0)
    records_screened = prisma.get("records_screened", 0)
    studies_included = prisma.get("studies_included", 0)
    duplicates_removed = prisma.get("duplicates_removed", 0)
    irrelevant_excluded = prisma.get("excluded_title_abstract", 0) + prisma.get("excluded_full_text", 0)

    paper_records = {p["paper_id"]: p for p in (values.get("paper_records") or [])}
    evidence_records = values.get("evidence_records") or []

    evidence_rows = []
    for record in evidence_records[:4]:
        paper = paper_records.get(record.get("paper_id"), {})
        evidence_rows.append({
            "study": _study_label(paper),
            "focus": record.get("task_or_domain") or (record.get("claim_summary") or "")[:48],
            "methodology": (paper.get("study_type") or "empirical").replace("_", " ").title(),
            "finding": record.get("claim_summary") or "",
            "level": _evidence_level(record),
        })

    source_names = sorted((prisma.get("records_by_source") or {}).keys())

    return {
        "id": meta["id"],
        "topic": meta["topic"],
        # Landing page shows this as the "input research question", so keep the
        # crisp prompt the user would actually type - not the pipeline's expanded
        # problem statement, which runs to ~1000 chars and overflows the card.
        "question": meta["question"],
        "stats": {
            "discovered": records_identified,
            "screened": records_screened,
            "included": studies_included,
        },
        "prisma": {
            "identification": {
                "stat": f"{records_identified} Records Discovered",
                "sub": _source_breakdown(prisma.get("records_by_source") or {}),
            },
            "dedup_excluded": {
                "stat": f"{duplicates_removed} Excluded",
                "sub": "Identical DOIs & normalized title matches",
            },
            "screening": {
                "stat": f"{records_screened} Records Screened",
                "sub": "Abstract relevance scored against research scope",
            },
            "irrelevant_excluded": {
                "stat": f"{irrelevant_excluded} Excluded",
                "sub": "Score below relevance threshold against criteria",
            },
            "included": {
                "stat": f"{studies_included} Studies Included",
                "sub": "Full text resolved for final synthesis & matrix",
            },
        },
        "evidence_rows": evidence_rows,
        "paper": {
            "title": values.get("title") or meta["topic"],
            "meta": f"CiteProof Synthesis • {studies_included} Included Studies • Indexed via {', '.join(source_names) if source_names else 'multiple academic indexes'}",
            "abstract": values.get("abstract") or "",
            "body": (values.get("literature_review") or "")[:900],
            "citations": (values.get("references") or [])[:5],
        },
    }


async def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with AsyncSqliteSaver.from_conn_string(str(DB_PATH)) as saver:
        await saver.setup()
        set_checkpointer(saver)
        graph = get_research_mode_graph()

        results = []
        for meta in EXAMPLES:
            print(f"\n=== Running: {meta['topic']} ===", flush=True)
            values = await run_to_completion(graph, meta["question"])
            example = extract_example(meta, values)
            assert example["evidence_rows"], f"No evidence rows extracted for {meta['topic']!r}"
            assert example["paper"]["abstract"], f"No abstract extracted for {meta['topic']!r}"
            results.append(example)
            print(f"=== Done: {meta['topic']} ({example['stats']}) ===", flush=True)

    OUTPUT_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {len(results)} examples to {OUTPUT_PATH}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
