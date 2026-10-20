# Security Policy

## Supported versions

CiteProof is under active development. Security fixes land on `main` only; there are no maintained release branches. If you run a fork or a pinned commit, update to the latest `main` to get fixes.

## Reporting a vulnerability

Please do not open a public issue, pull request, or discussion for a security problem.

1. Use GitHub's private reporting: open the [Security tab](https://github.com/Aryan-Pardeshi/CiteProof/security/advisories/new) and choose **Report a vulnerability**.
2. If that option is not available, contact the maintainer through the links on the [author's GitHub profile](https://github.com/Aryan-Pardeshi) and ask for a private channel. Do not include exploit details in the first message.

A useful report includes:

- The affected component and file or route, and the commit you tested.
- Steps to reproduce or a proof of concept.
- The impact you expect (for example key disclosure, remote request forgery, or data loss).
- A suggested fix, if you have one.

### What to expect

This is a solo-maintained project, so these are targets, not guarantees:

| Step | Target |
|---|---|
| Acknowledge your report | within 5 days |
| Initial assessment (accepted, needs more info, or declined) | within 14 days |
| Fix or mitigation for a confirmed issue | as soon as practical, high severity first |

Please give us a reasonable window to fix the issue before disclosing it publicly. Once a fix is released, you are welcome to publish details, and we will credit you in the advisory unless you prefer to stay anonymous.

## Scope

In scope:

- The FastAPI backend in `backend/`, including the `/config/setup` endpoint. It rewrites `.env` and reloads the LLM clients, so it is designed to fail closed (`ALLOW_OPEN_CONFIG_API` for local use, `CONFIG_API_TOKEN` when deployed). A way around that check is a valid finding.
- Handling of secrets: API keys read from the environment, written to `.env`, or echoed in responses, logs, or exports.
- Server-side request handling, such as a user-controlled URL reaching `LLM_BASE_URL`, a paper full-text fetch, or another outbound request in a way that can reach internal addresses.
- The exporters (DOCX, PDF) and anything that renders content from retrieved papers or model output.
- The frontend in `frontend/` (for example stored or reflected XSS).
- The Docker and GitHub Actions configuration in this repository.

Out of scope:

- Vulnerabilities in third-party services the app calls (LLM providers, OpenAlex, PubMed, Crossref, Tavily, and similar). Report those to the provider.
- Vulnerabilities in a dependency that cannot be reached through this project. Dependabot and CodeQL already track these.
- A permissive CORS policy or missing authentication on a deployment where you chose to expose the app publicly without setting `CONFIG_API_TOKEN` or a reverse proxy.
- Prompt injection that only changes the wording of a generated review, with no effect on secrets, files, or outbound requests. Model output is not trusted input, but quality issues belong in a normal issue.
- Denial of service from sending very large volumes of requests, or from using your own API quota.
- Reports from automated scanners with no demonstrated impact.

## Handling secrets

Never commit API keys or a real `.env` file; `.env` is gitignored and `.env.example` shows the variables. If you find a live key in this repository or its history, report it as above so it can be revoked and removed.

## Safe harbor

Good-faith research that stays within this scope, avoids other people's data, and does not degrade service for others will not be pursued. Test against your own local instance, not against a hosted deployment you do not own.
