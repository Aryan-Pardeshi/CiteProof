import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.agents.research_mode.agents import verify_citations


def main():
    papers = [
        {"authors": ["John Smith", "Alice Johnson"], "year": 2024, "title": "Real Paper 1"},
        {"authors": ["Robert Jones"], "year": 2022, "title": "Real Paper 2"}
    ]

    lit_review_text = (
        "Recent advances in quantum neural networks have demonstrated high accuracy (Smith et al., 2024). "
        "However, legacy benchmarks (Fabricated et al., 2023) suffered from overfitting. "
        "Further analysis by (Jones, 2022) confirmed these findings, while (FakeAuthor & Other, 2021) claimed otherwise. "
        "Another section re-iterated (Fabricated et al., 2023) findings."
    )


    print("=== INPUT LITERATURE REVIEW ===")
    print(lit_review_text)
    print("\n=== PAPERS METADATA ===")
    for p in papers:
        print(p)

    modified_text, unverified = verify_citations(lit_review_text, papers)

    print("\n=== MODIFIED LITERATURE REVIEW ===")
    print(modified_text)

    print("\n=== UNVERIFIED CITATIONS FLAGGED ===")
    print(unverified)

if __name__ == "__main__":
    main()
