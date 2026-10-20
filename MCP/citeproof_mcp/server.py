# SPDX-License-Identifier: MIT
"""Server assembly. Every tool module exposes ``register(server)``."""

from citeproof_mcp.compat import MCPServer
from citeproof_mcp.tools import (
    core,
    evidence_checks,
    extraction,
    prisma,
    protocol,
    review,
)

INSTRUCTIONS = (
    "Audit tools for a systematic review. You make every judgement (include or exclude, claim supported or "
    "not, risk of bias, extracted values); these tools store it, check the evidence it points to against the "
    "source text you supply (quote_verified), compute PRISMA counts and refuse malformed input. Nothing here "
    "searches the literature or reads papers for you.\n"
    "Usual order: review_create -> protocol_record -> prisma_record_search (once per database search) -> "
    "dedup_papers (all exported records, with review_id) -> screening_record / screening_record_many at "
    "title_abstract, then full_text (two reviewers: record each, run screening_agreement, record the consensus "
    "as reviewer 'primary') -> evidence_record per included study and outcome -> rob_record per domain, "
    "rob_summary -> claim_record for the report's key claims, claim_report -> verify_dois and "
    "resolve_citations on the reference list -> prisma_flow_diagram, prisma_checklist, lint_prose on the "
    "draft -> export_bundle. Call review_status at any time for what is missing and the next steps.\n"
    "paper_ref identifies a paper everywhere: a DOI ('10.1234/abc'), 'pmid:<digits>' or "
    "'title:<title>|<year>'; use the same form for the same paper in every tool. Every result has ok; when ok "
    "is false, error says what to change."
)

TOOL_MODULES = (core, protocol, prisma, extraction, evidence_checks, review)


def build_server() -> MCPServer:
    server = MCPServer("citeproof", instructions=INSTRUCTIONS)
    for module in TOOL_MODULES:
        module.register(server)
    return server
