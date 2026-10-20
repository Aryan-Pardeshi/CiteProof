# CiteProof MCP

Audit tools for AI literature reviews, as an [MCP](https://modelcontextprotocol.io) server. It runs next to a research agent such as Claude Science and keeps the records a systematic review needs: a protocol, a PRISMA record, a screening log, an evidence table, claim checks, risk-of-bias records and an exportable audit trail.

**The server has no language model inside.** The agent searches, reads, decides and writes. CiteProof MCP stores each decision, checks the evidence it points to, does the counting and refuses bad input. It needs no API key and nothing leaves your machine, except DOI lookups to Crossref and doi.org.

## Contents

- [What it checks](#what-it-checks)
- [The 24 tools](#the-24-tools)
- [Install](#install)
- [A short review, step by step](#a-short-review-step-by-step)
- [What it does not do](#what-it-does-not-do)
- [Tests](#tests)
- [More documentation](#more-documentation)
- [Licence](#licence)

## What it checks

| Check | How |
|---|---|
| A quote really appears in the source text | The agent sends the quote and the text. The server matches them after normalising case, whitespace, curly quotes, dashes, zero-width characters and line-break hyphens. It needs word boundaries and at least 12 characters. The result is stored as `quote_verified`. |
| Numbers in the evidence table come from the quote | Every number entered (sample size, estimate, confidence limits) must be written in the quote. Estimates outside their interval and ratios of zero or less are refused. |
| PRISMA numbers add up | Counts are computed from the recorded searches, the deduplication step and the screening decisions. `PRISMATracker` invariants are checked on every call. |
| Two reviewers agree | Cohen's kappa over the papers both reviewers decided, with the list of disagreements. |
| DOIs exist and are not retracted | Crossref and doi.org lookups. An unreachable registry gives "unverified", never "not found". |
| Citations match the reference list | In-text citations are matched to your reference list by DOI, author and year, or title. "Resolved" means matched, not supported. |
| Risk-of-bias overall judgements follow the published rules | RoB 2, ROBINS-I and Newcastle-Ottawa (AHRQ thresholds). The server computes the overall result from the domains. |
| Duplicates | Deterministic clustering by DOI, PMID, then title plus year plus first author. |

## The 24 tools

| Group | Tools |
|---|---|
| Set up and status | `review_create`, `review_list`, `protocol_record`, `review_status` |
| Searches and duplicates | `prisma_record_search`, `dedup_papers`, `prisma_record_dedup` |
| Screening | `screening_record`, `screening_record_many`, `screening_list`, `screening_agreement` |
| PRISMA | `prisma_status`, `prisma_flow_diagram`, `prisma_checklist` |
| Evidence and bias | `evidence_record`, `evidence_table`, `claim_record`, `claim_report`, `rob_record`, `rob_summary` |
| Citations and prose | `verify_dois`, `resolve_citations`, `lint_prose` |
| Export | `export_bundle` |

Every argument, result field and rule is described in [docs/SPEC.md](docs/SPEC.md). The agent also sees a description on every tool and argument.

## Install

**Claude Science (Windows):** follow [docs/CLAUDE_SCIENCE.md](docs/CLAUDE_SCIENCE.md). The connector runs in a sandbox, so the install copies the server into the connector's own folder.

**Any other MCP client:**

```bash
pip install ./MCP
```

Then point the client at the `citeproof-mcp` command (stdio). Example for a client with a JSON config:

```json
{
  "mcpServers": {
    "citeproof": {
      "command": "citeproof-mcp",
      "env": { "CITEPROOF_MCP_HOME": "/path/to/your/review-data" }
    }
  }
}
```

Review files go to `CITEPROOF_MCP_HOME`, or to `~/.citeproof-mcp` when it is not set. Python 3.12 or newer. The package works with `mcp` 1.27 and with `mcp` 2.x.

## A short review, step by step

1. `review_create` with the title, the question and the allowed publication years. Keep the `review_id`.
2. `protocol_record` with the PICO fields, the eligibility criteria, the databases and the search strategy. It lists what is still missing.
3. After each database search, `prisma_record_search` with the source, the query, the date and the number of hits.
4. `dedup_papers` with the exported records. It records the duplicate count itself.
5. `screening_record` or `screening_record_many` for every decision at title and abstract, then at full text. A second reviewer records under another name; `screening_agreement` gives kappa.
6. `evidence_record` for each included study and outcome, with the quote and its source text. `rob_record` for each risk-of-bias domain, then `rob_summary`.
7. Write the report. Use `claim_record` for the cited claims, `verify_dois` and `resolve_citations` for the references, `prisma_flow_diagram` and `prisma_checklist` for the methods.
8. `review_status` at any point says which phase you are in and what to do next. `export_bundle` returns the whole audit trail as one document.

## What it does not do

- It does not search for papers. Use your agent's search, or the CiteProof Research app in the parent repository.
- It does not judge anything. A label such as "supported" is the agent's judgement. The server only checks that the quote is in the text.
- It does not read PDFs. The agent supplies the text.
- The PRISMA checklist and the report number check are keyword checks. They show what is mentioned, not whether it is reported well.
- Deduplication matches titles exactly after normalising. It does not do fuzzy matching.

## Tests

The tests are offline. They use the MCP in-memory client and an isolated data folder.

```bash
cd MCP
pip install -e ".[test]"
python -m pytest -q
```

## More documentation

- [docs/SPEC.md](docs/SPEC.md): the full tool reference and design rules.
- [docs/CLAUDE_SCIENCE.md](docs/CLAUDE_SCIENCE.md): install in Claude Science, update and troubleshooting.
- [NOTICE.md](NOTICE.md): third-party code.

## Licence

MIT, see the [root licence](../LICENSE). `citeproof_mcp/_vendor/doi_verify.py` and `prose_lint.py` are adapted from Anthropic's Claude Science `literature-review` skill under the Apache License 2.0. See [NOTICE.md](NOTICE.md) and [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt).
