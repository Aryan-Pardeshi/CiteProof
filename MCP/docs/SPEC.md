# MCP server spec: review audit tools for Claude Science

Status: version 0.1.0, 2026-10-20. A small first benchmark (two reviews) showed Claude Science far ahead of CiteProof Research on finding the known included studies, so this server does not try to search. It runs next to a research agent and audits its work.

## Goal

A local MCP server (stdio, added in Claude Science through Connectors, "Add connector", "Local command") that gives a review agent the parts a systematic review needs and Claude Science does not do well: a protocol record, a PRISMA record with computed deduplication, a screening log with reviewer agreement, an evidence table with checked quotes, checked claim support, risk-of-bias records with the published overall rules, verified citations, and one export of the whole audit trail, all on disk.

Claude Science stays the researcher: it searches, reads and writes. Our server keeps the records, checks them and refuses bad input.

## Design rules

1. **The server contains no language model.** The calling agent makes every judgement (include or exclude, claim supported or not, risk of bias, extracted values). The server stores the judgement, validates its shape, checks the evidence it points to, and computes numbers from it. No API key, no model cost, no second model to disagree with the first.
2. **Never trust a quote.** Where a tool takes a quote and the source text, the server checks that the quote appears in the source text and stores `quote_verified`. Matching ignores case, whitespace, curly quotes, dash variants, zero-width characters, line-break hyphenation and hyphens inside words; it needs at least 12 normalised characters and matches on word boundaries for scripts written with spaces. Ellipsis quotes (`a ... b`, `a [...] b`) verify only when every part is found in order, at most 1500 characters apart. In the evidence table every number entered must also be written in the quote (`numbers_not_in_quote`). A failed check is stored and reported, not hidden.
3. **Honest wording.** Labels say "recorded by the agent" or "checked against the supplied text". Nothing says "verified" unless a registry or a string match confirmed it. Citation matching is "resolved", not "verified". A DOI the registry could not be reached for is "unverified", never "not found".
4. **Deterministic.** Same input, same output. Counts, invariants, kappa, duplicate clusters and overall risk-of-bias judgements are computed, never estimated.
5. **Small tools.** One job per tool, flat JSON arguments with a description on every argument, JSON results. Every result has `ok: bool`; failures return `ok: false` with `error` text the agent can act on. Tools never raise: unexpected errors become `internal error in <tool> (<ExceptionType>)` with no stack trace and no file path. (Arguments of the wrong JSON type, for example a string where the schema says integer, are rejected by the MCP SDK's schema validation before the tool runs; that comes back as an MCP tool error.)
6. **Local only.** State lives in SQLite files under `CITEPROOF_MCP_HOME` (default `~/.citeproof-mcp`). The only network calls are DOI checks to `api.crossref.org` and `doi.org` in `verify_dois`: fixed HTTPS hosts, redirects are never followed (a redirect from doi.org is read as "registered"), 15 second timeout, DOI path segments percent-encoded, and a value that is not DOI-shaped is never sent. `tests/test_doi_hosts.py` checks this. Nothing is sent to us. Exports are returned as text; nothing is written outside the home folder.
7. **Idempotent where possible.** Recording the same search, screening decision, claim, evidence row or risk-of-bias domain twice updates it instead of duplicating it.

## Stack

- Python 3.12 with the official `mcp` SDK 2.x in the repo (`from mcp.server.mcpserver import MCPServer`). The server must also run under mcp 1.x (Claude Science's bundled Python 3.13, mcp 1.27), where the same class is `mcp.server.fastmcp.FastMCP`; `citeproof_mcp/compat.py` picks whichever exists. Tool code uses only APIs present in both.
- Tool modules do not use `from __future__ import annotations`; argument descriptions are `Annotated[type, pydantic.Field(description=...)]`, which both SDK versions turn into the JSON schema.
- stdio transport: `python -m citeproof_mcp`, or the `citeproof-mcp` command after `pip install ./MCP`. `scripts/build_claude_science_bundle.py <folder>` builds a self-contained copy for Claude Science (see [CLAUDE_SCIENCE.md](CLAUDE_SCIENCE.md)).
- The four modules the core tools need (`evidence`, `doi_verify`, `prose_lint`, `validation`) are copied into `citeproof_mcp/_vendor/` from the research pipeline, so the package has no dependency on the rest of the repository.
- Tests use the in-memory client: `async with Client(server) as c: await c.call_tool(name, args)` (helper `call()` in `tests/conftest.py`).

## Code layout

| Module | Tools | Tables owned |
|---|---|---|
| `store.py` | (none) review files, schema registry, paper_ref canonical form | `meta` |
| `tools/common.py` | (none) never-raise wrapper `safe_tool`, argument checks, list caps, Markdown cells | |
| `tools/quotes.py` | (none) quote matching and number extraction | |
| `tools/core.py` | review_create, review_list, verify_dois, resolve_citations, lint_prose | `doi_checks` |
| `tools/protocol.py` | protocol_record | `protocol`, `protocol_changes` |
| `tools/prisma.py` | prisma_record_search, dedup_papers, prisma_record_dedup, screening_record, screening_record_many, screening_list, screening_agreement, prisma_status, prisma_flow_diagram, prisma_checklist | `prisma_searches`, `prisma_dedup`, `screening` |
| `tools/prisma_render.py` | (none) flow diagrams, PRISMA 2020 checklist rules, report number checks | |
| `tools/dedup.py` | (none) the duplicate-clustering algorithm behind dedup_papers | |
| `tools/extraction.py` | evidence_record, evidence_table | `evidence_rows` |
| `tools/evidence_checks.py` | claim_record, claim_report, rob_record, rob_summary | `claims`, `rob` |
| `tools/review.py` | review_status, export_bundle | (none; reads the others through their public read functions) |

## State

One SQLite file per review: `<home>/reviews/<review_id>.db`. `store.create_review` creates the file (WAL journal) and the `meta` table. Each tool module declares its `CREATE TABLE IF NOT EXISTS` statements (and any `ALTER TABLE ... ADD COLUMN` upgrade) with `store.register_schema`; `store.open_review` applies them the first time a file is opened in the process. Modules never query each other's tables; they call each other's public read functions (`prisma.compute_status`, `prisma.included_refs`, `extraction.evidence_rows`, `evidence_checks.rob_overview`, ...).

Concurrency: connections use a 30 s busy timeout, files use WAL, and every write runs in one `BEGIN IMMEDIATE` transaction (`store.review_db(review_id, write=True)`), so concurrent calls on one review wait instead of failing with "database is locked".

Paper identity: tools accept a `paper_ref` string and store its canonical form (`store.canonical_paper_ref`): a DOI, lower-cased, with any `doi:` or doi.org prefix removed; `pmid:<digits>`; or `title:<normalised title>|<year or nd>` (accents folded, punctuation removed, letters of every script kept). Anything else is refused. `store.normalize_paper_ref(doi, pmid, title, year)` builds one from parts.

`review_id` is validated against `^[a-z0-9_-]{6,40}$` before it becomes a file name.

## Workflow

The server instructions give the agent this order: review_create, protocol_record, prisma_record_search (each search), dedup_papers, screening at title_abstract then full_text (two reviewers: each under their own name, screening_agreement, consensus under `primary`), evidence_record, rob_record and rob_summary, claim_record and claim_report, verify_dois and resolve_citations, prisma_flow_diagram, prisma_checklist and lint_prose on the draft, export_bundle. `review_status` reports the current phase and next steps at any point.

## Tools (24)

Arguments marked `?` are optional. Every result also has `ok`. Lists are capped at 200 items with `truncated: true` when cut, unless stated.

### Core (`tools/core.py`)

- `review_create(title, question, year_min?, year_max?)` → `review_id, next_step`. Title max 500 characters, question required (max 2000), years 1800 to 3000 and ordered.
- `review_list(limit? = 50)` → `reviews[] (review_id, title, question, created_at, year_min, year_max), count`. Newest first.
- `verify_dois(dois, review_id?)`: `dois` is a list of up to 100 DOIs or one string of DOIs separated by spaces, commas or new lines; DOIs and doi.org URLs are normalised and de-duplicated case-insensitively. Wraps `citeproof_mcp._vendor.doi_verify.verify_dois`. → `results[] (doi, input, status, title, year, journal, authors, retracted, registry), summary (checked, resolved, not_found, unverified, invalid, retracted), offline, saved, note`. Status: `resolved`, `not_found` (doi.org 404), `unverified` (registry unreachable: no verdict), `invalid` (not DOI-shaped; never sent to a registry). `retracted` is true/false only for Crossref records. With `review_id` the results are saved (`doi_checks`) for review_status and export_bundle. If the library itself fails, `ok: false` and nothing is judged.
- `resolve_citations(text, papers[])`: wraps `_vendor.validation.resolve_citations` after rewriting common author-year forms into `(Author, 2020)` (`Smith et al. (2020)`, `(Smith 2020)`, `(Smith, 2020a)`, `(A, 2020; B and C, 2021)`, `e.g.` prefixes, page suffixes; groups with DOIs or URLs are left alone). `papers` up to 2000 objects with title, authors (list or `A; B`), year, doi?. → `citations_found, resolved, ambiguous[], unresolved[], orphan_papers[] (titles), doi_citations_checked, numeric_citations_found, truncated, note`. `resolved` excludes ambiguous matches. Numeric citations (`[1]`) are counted, not resolved.
- `lint_prose(text)`: wraps `_vendor.prose_lint.lint_prose`. → `issues[] (code, note), issue_count, advisory: true`.

### Protocol (`tools/protocol.py`)

- `protocol_record(review_id, population?, intervention?, comparator?, outcomes?, study_designs?, inclusion_criteria?, exclusion_criteria?, databases?, search_strategy?, languages?, registration?, rob_tool?, synthesis_method?, year_min?, year_max?)`. Omitted fields keep their value (so `protocol_record(review_id)` reads the protocol); `''` or `[]` clears a field. List fields accept a list or one string split on new lines and semicolons. `rob_tool` is one of rob2, robins_i, nos, nos_case_control, mixed, none. Every change is logged in `protocol_changes`; a change to a field that already had a value is an amendment. → `protocol, updated_fields, complete, missing_required, missing_recommended, amendments, next_step`. Required: population, intervention, outcomes, inclusion_criteria, exclusion_criteria, databases, search_strategy. Recommended: comparator, study_designs, year_range, languages, registration, rob_tool, synthesis_method.

### PRISMA and screening (`tools/prisma.py`)

- `prisma_record_search(review_id, source, query, date_run, hits)`: one row per database search; the same source + query + date_run replaces the hit count (a retried call cannot double-count); source names match case-insensitively. `date_run` must be a real YYYY-MM-DD date (a future date is a warning). Adding a search, or changing a search's hit count, clears the review's deduplication record (the old count belonged to the old set of records) and says so in a warning; re-sending a search with the same hits changes nothing. → `search_id, updated, records_identified, records_by_source, searches_recorded, dedup_cleared, warnings`.
- `dedup_papers(papers[], review_id?)`: deterministic duplicate detection over up to 10000 exported records (`id?, doi?, pmid?, title?, year?, authors? | first_author?, abstract?`). Same paper = same normalised DOI, or same PMID, or same normalised title (20+ characters) with the same year and first-author surname; matching is transitive; the record with the most complete metadata is kept. Same title with a different year or author is not merged but listed. A review has exactly one dedup count, so with `review_id` the count is recorded only when the call covers every exported record: `records_checked` must equal the hits recorded with `prisma_record_search`. A partial or batched call (for example two halves of one export) is analysed but not recorded (`recorded: false`, a warning), and it never replaces an earlier record. With no searches recorded the call is refused. More than 10000 records are refused, because duplicates across separate calls would be missed; deduplicate such a set elsewhere and record the whole-review count with `prisma_record_dedup`. Records are not merged: the agent must copy any field it needs from the removed records onto the kept one before discarding them (the result's `note` says so). → `records_checked, unique_records, duplicates_removed, removed_indices (all), clusters[] (kept_index, kept_id, member_indices, matched_on, conflicting_dois), possible_duplicates[] (max 50), truncated, recorded, method, warnings, note`.
- `prisma_record_dedup(review_id, duplicates_removed, method)`: for deduplication done elsewhere; replaces the dedup record; cannot exceed the recorded hits. → `duplicates_removed, records_identified, records_after_dedup, method`.
- `screening_record(review_id, paper_ref, stage, decision, reason?, reviewer? = 'primary', title?)`: `stage` is `title_abstract` or `full_text`; `decision` is `include` or `exclude`, and at full_text also `not_retrieved`; a reason (max 300 characters) is required for `exclude`; reviewer names are 1 to 40 characters, stored lower-case. Upserts on (paper_ref, stage, reviewer). → `updated, paper_ref, stage, reviewer, decision, stage_counts, warnings` (full_text decision without a title_abstract include; title_abstract exclusion of a paper that has a full_text decision).
- `screening_record_many(review_id, decisions[])`: up to 500 items with the same fields; all-or-nothing; an invalid item or a repeated (paper_ref, stage, reviewer) returns `{ok: false, error, index}`. → `count, inserted_count, updated_count, warning_count, warnings[] (max 50)`.
- `screening_list(review_id, stage?, reviewer?, decision?, paper_ref?, limit? = 200, offset? = 0)` → `records[], count, total, truncated, next_offset`. Newest first.
- `screening_agreement(review_id, stage, reviewer_a, reviewer_b)`: Cohen's kappa over papers both reviewers decided with include or exclude (`not_retrieved` pairs counted in `not_judged`). `kappa: null`, interpretation `undefined` when nothing is shared or pe = 1. Landis and Koch bands. → `shared, agree, table (both_include, a_include_b_exclude, a_exclude_b_include, both_exclude), po, pe, kappa, interpretation, disagreement_count, disagreements[], truncated, only_a, only_b, not_judged, next_step`.
- `prisma_status(review_id, reviewer?)`: PRISMA 2020 counts from the searches, the dedup record and one reviewer's decisions (the named reviewer, else `primary`, else the only reviewer; several reviewers without `primary` is an error; a named reviewer with no decisions is an error that lists the known reviewers). Reports sought = title_abstract includes; reports not retrieved = full_text `not_retrieved`; assessed = full_text include + exclude; reports sought with no full_text decision yet are `full_text_pending`. → `reviewer_used, records_identified, records_by_source, duplicates_removed, records_after_dedup, records_screened, excluded_title_abstract, full_text_requested, full_text_unavailable, full_text_assessed, excluded_full_text, studies_included, exclusion_reasons (full-text), exclusion_reasons_title_abstract, full_text_pending, records_awaiting_screening, full_text_without_title_abstract_include[] (max 20), searches_recorded, dedup_recorded, dedup_method, invariants_valid, invariant_errors, warnings, complete, next_steps`. Invariants: `PRISMATracker.validate_invariants()` plus "full_text decisions only for title_abstract includes" (otherwise sought != assessed + not retrieved + pending). `complete` is true when searches and dedup are recorded, every record after dedup has a title_abstract decision, no full-text decision is pending and the invariants hold.
- `prisma_flow_diagram(review_id, format? = 'mermaid', reviewer?)`: `mermaid`, `svg` or `markdown`, text only. Agent text is escaped for the format (Mermaid entity codes `#quot;`, `#lt;`, `#gt;`; XML escaping in SVG; no raw HTML in Markdown). Shows the top full-text exclusion reasons and a pending box when needed. → `format, diagram, complete, warnings`.
- `prisma_checklist(review_id, report_text, reviewer?)`: keyword check against the 27 PRISMA 2020 items (Page et al., BMJ 2021, numbered as published: 23 Discussion, 24 Registration and protocol, 25 Support, 26 Competing interests, 27 Availability of data, code and other materials); phrases match on word boundaries; item 1 checks the first line for "systematic review" or "meta-analysis". Also finds sentences stating PRISMA counts ("1,234 records were identified", "12 studies were included", ...) and compares them with the recorded counts. → `found_count, total, items[] (item, section, topic, found, matched), missing[], numbers[] (field, reported, recorded, matches, snippet; max 50), number_mismatches, numbers_note, note` ("Keyword check ..., not whether they are reported well"). Nothing is saved.

### Evidence table (`tools/extraction.py`)

- `evidence_record(review_id, paper_ref, outcome, study_design, quote, source_text, sample_size?, population?, intervention?, comparator?, timepoint?, effect_measure?, effect_estimate?, ci_lower?, ci_upper?, ci_level?, notes?)`: one row per (paper_ref, outcome), upserted. `effect_measure` is rr, or, hr, irr, rom, rd, md, smd or other (names such as "risk ratio" are accepted). Refused: an estimate without a measure, only one CI limit, lower > upper, an estimate outside its CI, a ratio measure with a value <= 0, a non-finite number, an empty source_text. Stored and flagged: a quote not found in source_text, numbers not written in the quote. Warns when paper_ref is not among the studies included at full_text. Only the length and SHA-256 of source_text are stored. → `updated, paper_ref, outcome, quote_verified, quote_note, numbers_not_in_quote, effect, rows_for_paper, warnings`.
- `evidence_table(review_id, format? = 'markdown', paper_ref?)`: `markdown` or `csv` return the whole table as text in `table` (CSV text cells starting with `= + - @` are prefixed with `'`); `json` returns `rows[]`. → `format, row_count, table | rows, truncated, included_without_rows[] (max 50), included_without_rows_count, rows_quote_not_verified, rows_numbers_not_in_quote`.

### Claim support and risk of bias (`tools/evidence_checks.py`)

- `claim_record(review_id, claim_text, paper_ref, label, reason, source_text? = '', quote? = '')`: `label` is `supported`, `partial` or `unsupported`; label and reason are required. `supported` and `partial` need a quote and a non-empty source_text. Upserts on (normalised claim text, paper_ref). Stores `quote_verified`, the source length and SHA-256, never the source text. → `claim_id, updated, quote_verified, quote_note, warnings`.
- `claim_report(review_id, report_text?)` → `total, counts_by_label, quotes_not_verified (claims with a quote that did not verify), suspicious_count, suspicious[] (supported or partial claims whose quote did not verify), unsupported_count, unsupported[], weak_claims_in_report[] (suspicious or unsupported claims whose text appears verbatim in report_text; null without report_text), truncated, method, next_step`.
- `rob_record(review_id, paper_ref, tool, domain, judgement, quote? = '', source_text? = '', rationale?)`: `tool` is `rob2`, `robins_i`, `nos` (cohort) or `nos_case_control`. Fixed domains per tool (rob2 D1-D5 and robins_i D1-D7 aliases accepted); judgements rob2 low / some_concerns / high, robins_i low / moderate / serious / critical / no_information, NOS star counts (selection 0-4, comparability 0-2, outcome or exposure 0-3, as string or integer). rob2 and robins_i also accept domain `overall` (the reviewer's own overall judgement; rationale required, quote optional). A quote is required except for `overall`, robins_i `no_information` and 0 stars. A paper cannot mix tools. → `updated, paper_ref, tool, domain, judgement, quote_verified, quote_note, domains_recorded, domains_missing, warnings`.
- `rob_summary(review_id)` → `papers[] (paper_ref, tool, domains, missing_domains, overall, overall_by_rule, overall_recorded, notes, total_stars | no_information_domains), table[] (traffic-light rows), counts_by_overall, papers_with_unverified_quotes[], overall_rule, truncated`. Rules:
  - RoB 2 (Sterne et al. 2019): any high gives high; else any some concerns gives some_concerns; else low. With some concerns in two or more domains the guidance allows an overall high: the result notes it, and a recorded `overall` of high is used. A recorded overall outside what the rule allows is ignored with a note.
  - ROBINS-I (Sterne et al. 2016): any critical gives critical; else any serious gives serious; else any no_information gives no_information (a recorded `overall` of low or moderate, not below the worst rated domain, is used when the missing domains are not key); else the worst of moderate and low.
  - NOS (cohort and case-control), AHRQ thresholds: good = selection 3-4, comparability 1-2, outcome/exposure 2-3; fair = selection 2 with the same comparability and outcome; poor otherwise.
  - Any tool: `incomplete` while a domain is missing.

### Whole review (`tools/review.py`)

- `review_status(review_id, reviewer?)` → `review, phase, protocol (complete, missing_required, missing_recommended), prisma (key counts, complete) | {error}, screening_reviewers, included_studies, extraction, risk_of_bias, claims, dois, warnings, next_steps (max 10)`. `phase` is the first unfinished step of: protocol, searching, deduplication, title_abstract_screening, full_text_screening, data_extraction, risk_of_bias, claim_checking, reporting. Coverage checks: included studies without evidence rows or risk-of-bias records, evidence rows for papers not included, included DOIs not yet checked, retracted or non-resolving DOIs, single-reviewer screening. Lists show the first 20 paper_refs. Read only.
- `export_bundle(review_id, format? = 'markdown', reviewer?)`: one document with review metadata, protocol and amendments, search log, dedup, PRISMA counts and invariants with a Mermaid diagram, screening decisions (max 5000), agreement for each reviewer pair at each stage, evidence table, risk of bias with rules, claims (max 5000), saved DOI checks, warnings and next steps. Source texts are never included. → `format, content, chars, suggested_filename, truncated, phase`. Nothing is written to disk.

### Later (not in this release)

`citation_chain`, `retraction_watch` (beyond the Crossref flag in verify_dois), `grade_certainty`, `meta_analysis`, reference formatting in a citation style.

## Result shape

```json
{"ok": true, "...": "tool specific fields"}
{"ok": false, "error": "stage must be one of: title_abstract, full_text; got 'full_pdf'"}
```

## Security

- No shell, no file reads outside `CITEPROOF_MCP_HOME`, no arbitrary URL fetch. DOI checks use the existing `doi_verify` module with fixed hosts.
- The agent supplies source text; the server treats it as data. Text is never executed, never used in SQL strings (parameterised queries only), and never echoed back in full. Source texts are stored only as length and SHA-256.
- Error messages never contain stack traces or file paths.
- Rendered output escapes agent text for its format (Mermaid, SVG/XML, Markdown, CSV formula prefixes).

## Acceptance

1. `python -m citeproof_mcp` starts and lists the 24 tools above, under mcp 2.x and under mcp 1.x (Claude Science's bundled Python 3.13 with mcp 1.27).
2. Offline tests pass for every tool, using the in-memory client and an isolated `CITEPROOF_MCP_HOME`: run `python -m pytest -q -p no:cacheprovider` from the `MCP/` folder.
3. Claude Science can add it as a local command and list all 24 tools. This was checked by hand. A full review run with every tool in Claude Science has not been benchmarked yet.
