import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

# Add the repo root to sys.path so 'backend' module is importable
root_dir = Path(__file__).resolve().parent
repo_root = root_dir.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from backend.app.graph.research_mode_builder import (
    get_research_mode_graph,
    set_checkpointer,
)

THREAD_FILE = Path("./data/latest_thread_id.txt")


async def run_pipeline(problem: str = "", resume_thread_id: str | None = None):
    db_path_str = os.getenv("RESEARCH_DB_PATH", "./data/research_state.db")
    db_path = Path(db_path_str).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
        await saver.setup()
        set_checkpointer(saver)
        graph = get_research_mode_graph()

        if resume_thread_id:
            thread_id = resume_thread_id
            config = {"configurable": {"thread_id": thread_id}}
            state = await graph.aget_state(config)

            if not state or not state.values:
                print(f"[ERROR] Thread {thread_id} not found in state database.")
                return

            stage_list = list(state.next) if state and state.next else []
            print(f"[RESUME] Resuming thread {thread_id} at stage: {stage_list}")

            if not state.next:
                print(f"[INFO] Thread {thread_id} has already completed processing.")
                return

            input_val = Command(resume={"message": "approve"})
        else:
            thread_id = str(uuid.uuid4())
            THREAD_FILE.parent.mkdir(parents=True, exist_ok=True)
            THREAD_FILE.write_text(thread_id, encoding="utf-8")
            print(f"[START] Initializing new thread: {thread_id}")

            config = {"configurable": {"thread_id": thread_id}}
            problem_statement = problem if problem.strip() else "Impact of AI on Healthcare 2026"
            input_val = {
                "thread_id": thread_id,
                "problem_statement": problem_statement,
                "research_objectives": [],
                "research_questions": [],
                "keywords": [],
                "raw_papers": [],
                "screened_papers": [],
                "status": "initializing"
            }

        async for event in graph.astream(input_val, config=config):
            print(f"Event: {list(event.keys())}")

        updated_state = await graph.aget_state(config)
        paused_next = list(updated_state.next) if updated_state and updated_state.next else 'END'
        print(f"Paused state next: {paused_next}")


def main():
    parser = argparse.ArgumentParser(description="Test runner for Research Mode pipeline with durable session state and resume capabilities.")
    parser.add_argument("--problem", type=str, default="", help="Problem statement for starting a new research run.")
    parser.add_argument(
        "--resume",
        nargs="?",
        const="latest",
        default=None,
        help="Resume execution of a paused thread. If passed without value, reads thread_id from ./data/latest_thread_id.txt."
    )
    args = parser.parse_args()

    resume_id = None
    if args.resume is not None:
        if args.resume != "latest" and args.resume.strip():
            resume_id = args.resume.strip()
        elif THREAD_FILE.exists():
            resume_id = THREAD_FILE.read_text(encoding="utf-8").strip()
        else:
            print(f"[ERROR] Cannot resume: --resume specified without target ID and {THREAD_FILE} does not exist.")
            sys.exit(1)

    asyncio.run(run_pipeline(problem=args.problem, resume_thread_id=resume_id))


if __name__ == "__main__":
    main()
