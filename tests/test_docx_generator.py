import os
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent
repo_root = root_dir.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from backend.app.tools.docx_generator import generate_paper_docx


def test_docx_generation():
    dummy_state = {
        "problem_statement": "Autonomous Multi-Agent Search and Rescue in Subterranean Environments",
        "title": "Autonomous Multi-Agent Robotics in Subterranean Search and Rescue",
        "abstract": "This study explores multi-agent coordination under communication constraints.",
        "keywords": ["Robotics", "Multi-Agent", "Subterranean"],
        "introduction": "Subterranean environments present severe operational challenges...",
        "literature_review": "Prior work by Smith et al. (2024) demonstrated...",
        "research_gap": "Limited real-time adaptivity under total comms loss.",
        "research_objectives": ["Develop decentralized consensus", "Evaluate search coverage"],
        "research_questions": ["How does loss of comms affect latency?"],
        "conceptual_framework": "Decentralized Graph Neural Network framework.",
        "hypotheses": ["H1: Decentralized routing reduces latency by 30%."],
        "research_design": "Experimental simulation benchmark.",
        "data_collection_plan": "Simulated sensor logs across 50 cavern trials.",
        "data_analysis_plan": "ANOVA variance analysis of coverage rates.",
        "results": "Decentralized agents achieved 94% coverage.",
        "discussion": "The findings confirm H1 predictions.",
        "implications": "Crucial for emergency response teams.",
        "limitations": "Simulated noise models may underrepresent extreme dust.",
        "conclusion": "Decentralized multi-agent systems significantly improve resilience.",
        "future_scope": "Field deployment in active mine shafts.",
        "references": ["Smith, J. et al. (2024). Robotics in Caverns. IEEE Trans."],
        "appendices": ["Appendix A: Simulation Hyperparameters"]
    }

    out_file = root_dir / "test_doc_output.docx"
    result_path = generate_paper_docx(dummy_state, str(out_file))

    assert os.path.exists(result_path), "DOCX file was not created"
    assert os.path.getsize(result_path) > 1000, "DOCX file is too small or empty"
    print(f"SUCCESS: DOCX generated at {result_path} ({os.path.getsize(result_path)} bytes)")

if __name__ == "__main__":
    test_docx_generation()
