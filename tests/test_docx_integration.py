import asyncio
import os
import sys
from pathlib import Path

import httpx
import pytest

root_dir = Path(__file__).resolve().parent
repo_root = root_dir.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from backend.app.graph.research_mode_builder import get_research_mode_graph


@pytest.mark.skip(reason="live integration test: runs the real graph against configured LLM/API keys, run manually with `python tests/test_docx_integration.py`")
async def test_integration():
    graph = get_research_mode_graph()
    thread_id = "test_docx_integration_thread"
    config = {"configurable": {"thread_id": thread_id}}

    # Seed state in checkpoint
    initial_input = {
        "problem_statement": "Multi-Agent UAV Coordination in Subterranean Rescue Operations",
        "research_objectives": ["Formulate robust routing under signal loss"],
        "research_questions": ["What is maximum tolerable comms latency?"],
        "keywords": ["UAV", "Multi-Agent", "Subterranean"]
    }

    # Run scope_definition node
    await graph.ainvoke(initial_input, config=config)
    state = await graph.aget_state(config)
    assert state.values.get("problem_statement") == initial_input["problem_statement"]

    # Test docx generator directly with state
    from backend.app.tools.docx_generator import generate_paper_docx
    out_file = root_dir / f"test_integration_{thread_id}.docx"
    res_path = generate_paper_docx(state.values, str(out_file))

    assert os.path.exists(res_path)
    assert os.path.getsize(res_path) > 1000
    print(f"Integration Test PASS: DOCX generated successfully at {res_path} ({os.path.getsize(res_path)} bytes)")

if __name__ == "__main__":
    asyncio.run(test_integration())
