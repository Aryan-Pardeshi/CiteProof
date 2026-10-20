# Third-party code in CiteProof MCP

## Adapted from Claude Science (Apache License 2.0)

Two files in `citeproof_mcp/_vendor/` are adapted from `kernel.py` of the `literature-review` skill in Anthropic's Claude Science app. The source is licensed under the Apache License, Version 2.0. The full licence text is in [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt).

| File | Adapted from | Changes |
|---|---|---|
| `citeproof_mcp/_vendor/doi_verify.py` | DOI extraction, Crossref and doi.org resolution, the Crossref retraction check | The `urllib` calls were rewritten as async `httpx` functions with an injectable client. The host SDK and the OpenAlex helpers were removed. The User-Agent identifies CiteProof MCP. |
| `citeproof_mcp/_vendor/prose_lint.py` | `style_pass` | Renamed to `lint_prose`, returns a typed frozen result, more process-narration patterns, no model parameter. |

Each file states its modification in its own header, as the licence requires.

## Dependencies

CiteProof MCP depends on `mcp` (MIT), `pydantic` (MIT) and `httpx` (BSD-3-Clause). The test extra adds `pytest` and `pytest-asyncio`. Check each project's own licence file for the exact terms.
