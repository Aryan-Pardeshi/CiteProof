import asyncio
import logging
import sys
import time
import uuid
from pathlib import Path

root_dir = Path(__file__).resolve().parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("test_acceptance_b_d_e")

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from backend.app.graph.research_mode_builder import (
    get_research_mode_graph,
    set_checkpointer,
)
from backend.app.tools.pdf_generator import generate_paper_pdf

PROBLEM_STATEMENT = "Autonomous Multi-Agent Robotics in Subterranean Search and Rescue Operations 2026"

SECTION_KEYS = [
    "title",
    "abstract",
    "keywords",
    "introduction",
    "literature_review",
    "research_gap",
    "research_objectives",
    "research_questions",
    "conceptual_framework",
    "hypotheses",
    "research_design",
    "data_collection_plan",
    "data_analysis_plan",
    "results",
    "discussion",
    "implications",
    "limitations",
    "conclusion",
    "future_scope",
    "references",
    "appendices",
]

async def run():
    db_path = Path("./data/research_state.db").resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
        await saver.setup()
        set_checkpointer(saver)
        graph = get_research_mode_graph()

        thread_id = str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id}}
        logger.info(f"Starting test thread: {thread_id}")

        input_val = {
            "thread_id": thread_id,
            "problem_statement": PROBLEM_STATEMENT,
            "research_objectives": [],
            "research_questions": [],
            "keywords": [],
            "raw_papers": [],
            "screened_papers": [],
            "status": "initializing"
        }

        pipeline_start = time.time()
        fulltext_node_duration = None

        # Step 1: Start graph
        logger.info("Initializing graph streaming...")
        async for event in graph.astream(input_val, config=config):
            for node_name in event:
                logger.info(f"Node completed: {node_name}")

        # Step 2: Loop while checkpoints remain
        step_count = 0
        while True:
            state = await graph.aget_state(config)
            if not state.next:
                logger.info("Graph completed all nodes.")
                break

            step_count += 1
            logger.info(f"Checkpoint #{step_count} encountered. Resuming next node(s): {list(state.next)}")
            cmd = Command(resume={"message": "approve"})
            
            t0 = time.time()
            async for event in graph.astream(cmd, config=config):
                for node_name in event:
                    t1 = time.time()
                    elapsed_node = t1 - t0
                    logger.info(f"Node completed: {node_name} (took {elapsed_node:.2f}s)")
                    if node_name == "fulltext_fetcher":
                        fulltext_node_duration = elapsed_node

        pipeline_duration = time.time() - pipeline_start
        logger.info(f"Total pipeline wall-clock time: {pipeline_duration:.2f}s")

        # Fetch final state values
        final_state = await graph.aget_state(config)
        values = final_state.values

        # -------------------------------------------------------------
        # ACCEPTANCE (b): Excerpts check in papers_summary / prompt
        # -------------------------------------------------------------
        print("\n" + "="*60)
        print("ACCEPTANCE (b): Full-text excerpts in screened papers")
        print("="*60)
        screened_papers = values.get("screened_papers", [])
        long_excerpts = [p for p in screened_papers if len(p.get("content_excerpt", "")) > 250]
        print(f"Total screened papers: {len(screened_papers)}")
        print(f"Papers with full-text excerpt (>250 chars): {len(long_excerpts)}")
        for idx, p in enumerate(long_excerpts):
            title = p.get("title", "")
            excerpt_len = len(p.get("content_excerpt", ""))
            print(f"  [{idx+1}] Length: {excerpt_len} chars | Source: {p.get('source')} | Title: {title[:70]}")

        # -------------------------------------------------------------
        # ACCEPTANCE (e): fulltext_fetcher_agent wall-clock time
        # -------------------------------------------------------------
        print("\n" + "="*60)
        print("ACCEPTANCE (e): Wall-clock time added by fulltext_fetcher_agent")
        print("="*60)
        print(f"fulltext_fetcher_agent execution time: {fulltext_node_duration:.2f}s" if fulltext_node_duration else "fulltext_fetcher_agent duration captured in logs")
        print(f"Pipeline total wall-clock time: {pipeline_duration:.2f}s")

        # -------------------------------------------------------------
        # ACCEPTANCE (d): 20 Sections completeness & PDF export
        # -------------------------------------------------------------
        print("\n" + "="*60)
        print("ACCEPTANCE (d): 20 Sections Completeness & PDF Export")
        print("="*60)
        populated = 0
        for sec in SECTION_KEYS:
            val = values.get(sec)
            has_val = bool(val)
            if has_val:
                populated += 1
            length_info = len(str(val)) if val else 0
            print(f"  - {sec:24s}: {'[OK]' if has_val else '[MISSING]'} ({length_info} chars/items)")

        print(f"\nSection completeness: {populated}/{len(SECTION_KEYS)} sections populated.")

        if values.get("unverified_citations"):
            print(f"\nUnverified citations flagged by citation_verifier_agent: {values.get('unverified_citations')}")
        else:
            print("\nUnverified citations flagged: None (all citations verified or n.d.)")

        # Export PDF
        pdf_dir = Path("./data/output").resolve()
        pdf_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = pdf_dir / f"research_report_{thread_id[:8]}.pdf"
        generate_paper_pdf(values, str(pdf_path))

        pdf_size = pdf_path.stat().st_size
        print("\nPDF Export Successful!")
        print(f"  File: {pdf_path}")
        print(f"  Size: {pdf_size} bytes ({pdf_size / 1024:.2f} KB)")

if __name__ == "__main__":
    asyncio.run(run())
