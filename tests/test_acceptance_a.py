import asyncio
import logging
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO)

from backend.app.tools.academic_search import search_academic_papers
from backend.app.tools.fulltext_fetcher import fetch_fulltext_excerpts


async def main():
    print("=== FETCHING REAL PAPERS FROM ACADEMIC SEARCH ===")
    keywords = ["transformers", "large language models"]
    raw_papers, stats = await search_academic_papers(keywords)
    print(f"Retrieved {len(raw_papers)} raw papers. {stats}")

    papers_with_pdf = [p for p in raw_papers if p.get("pdf_url")]
    print(f"Papers with pdf_url: {len(papers_with_pdf)}")

    print("\n=== FETCHING FULL-TEXT EXCERPTS ===")
    # Pass the full ranked list: papers without a direct pdf_url can still be
    # rescued by the OA resolvers.
    excerpts = await fetch_fulltext_excerpts(raw_papers)

    print(f"\nSuccessfully fetched excerpts for {len(excerpts)} papers:")
    for idx, (title, excerpt) in enumerate(excerpts.items()):
        print(f"\n[{idx+1}] Title: {title}")
        print(f"     Length: {len(excerpt)} characters")
        print(f"     Snippet: {excerpt[:150]!r}")

if __name__ == "__main__":
    asyncio.run(main())
