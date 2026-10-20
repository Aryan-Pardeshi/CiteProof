# Contributing to DeepResearch

Thanks for looking. Small fixes and big features are both welcome. If you are unsure whether something fits, open an issue first and ask.

## Where to start

- Browse issues labelled [`good first issue`](https://github.com/Aryan-Pardeshi/DeepResearch_AI/labels/good%20first%20issue) (small, well scoped) or [`help wanted`](https://github.com/Aryan-Pardeshi/DeepResearch_AI/labels/help%20wanted) (bigger, maintainer wants help).
- Comment on the issue before you start so two people do not build the same thing.
- Have your own idea? Open a feature request and describe the problem before the solution.

## Project map

| Path | What lives there |
|---|---|
| `backend/app/api/` | FastAPI routes and SSE streaming |
| `backend/app/graph/` | LangGraph state machines for DeepSearch and Research Mode |
| `backend/app/agents/` | Agent nodes (`research_mode/` holds the 24-agent pipeline) |
| `backend/app/tools/` | One module per external source or exporter (OpenAlex, PubMed, DOCX, PDF, ...) |
| `backend/app/models/` | Pydantic schemas (`PaperRecord`, `EvidenceRecord`, `ReviewClaim`, ...) |
| `frontend/` | Vanilla JS + CSS, no build step |
| `tests/` | pytest suite, offline by default |

A new academic source is usually the easiest meaningful contribution: copy an existing module in `backend/app/tools/` (for example `doaj_search.py`), implement the same interface, add provider unit tests in `tests/test_provider_adapters_unit.py` style, and register it in the discovery step.

## Dev setup

```bash
git clone https://github.com/<your-fork>/DeepResearch_AI.git
cd DeepResearch_AI
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install ruff
cp .env.example .env             # only needed to run the app, not the tests
uvicorn backend.app.main:app --reload --port 8000
```

Python 3.12 is the supported version. The frontend needs no build; open `http://localhost:8000`.

## Running tests

```bash
pytest
```

The default run is offline and fast. Tests that hit real external APIs are marked `live` and deselected; run them on purpose with `pytest -m live`. Please do not add tests that need a real API key to pass by default.

## Linting

Run Ruff before opening a pull request:

```bash
ruff check .
```

## Making a change

1. Fork the repo and branch from `main` (`fix/short-name`, `feat/short-name`).
2. Keep the change focused. One concern per pull request.
3. Add or update tests for behaviour you change. Bug fixes should include a test that fails without the fix.
4. Run `pytest` and make sure it passes.
5. Open the pull request and fill in the template.

### Commit and PR titles

We use conventional prefixes: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, optionally with a scope.

```
fix(crossref): handle records with no title
feat(tools): add Zenodo discovery provider
```

## Ground rules for this codebase

- **Do not overclaim.** The pipeline checks that citations resolve and that numbers match extracted evidence. It does not check that a source supports a sentence. Do not use words like "verified" in UI or docs for things the code does not prove.
- **Counts are never LLM-generated.** PRISMA numbers come from deterministic code and are asserted by `PRISMATracker.validate_invariants()`.
- **Tests stay offline.** Mock HTTP and LLM calls. Live checks go behind the `live` marker.
- **Never commit secrets.** `.env` is gitignored; use `.env.example` for new settings and document them in the README.
- **Frontend stays zero-build.** No bundler or framework without discussing it first.

## Reporting bugs and security issues

Use the bug report template. For anything security sensitive, do not open a public issue; use GitHub's "Report a vulnerability" under the Security tab.

## Conduct

Be kind and assume good faith. See the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

By contributing you agree your work is released under the project's [MIT License](LICENSE).
