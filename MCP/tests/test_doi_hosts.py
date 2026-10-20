# SPDX-License-Identifier: MIT
"""The DOI verifier only talks to Crossref and doi.org, never follows redirects, and times out."""

from __future__ import annotations

import httpx
import pytest
from citeproof_mcp._vendor import doi_verify

ALLOWED_HOSTS = {"api.crossref.org", "doi.org"}


@pytest.mark.asyncio
async def test_requests_stay_on_the_two_registries_even_for_hostile_dois() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(404)

    hostile = [
        "10.1000/plain",
        "10.1000/a/../../b",
        "10.1000/x?host=evil.example",
        "10.1000/x#@evil.example/",
        "10.1000/%2F%2Fevil.example/path",
        "10.1000/аbc",
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        await doi_verify.verify_dois(hostile, client=client)

    assert seen, "the mock registry was never called"
    for request in seen:
        assert request.url.host in ALLOWED_HOSTS, str(request.url)
        assert request.url.scheme == "https"


@pytest.mark.asyncio
async def test_a_redirect_from_the_registry_is_reported_not_followed() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host == "api.crossref.org":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "https://publisher.example/article"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        result = await doi_verify.verify_dois(["10.1000/registered"], client=client)

    assert "publisher.example" not in seen
    assert result["10.1000/registered"]["ok"] is True


def test_the_default_client_does_not_follow_redirects_and_has_a_timeout() -> None:
    assert doi_verify.DOI_VERIFY_TIMEOUT <= 30
    assert doi_verify.CROSSREF_WORKS_URL.startswith("https://api.crossref.org/")
    assert doi_verify.DOI_RESOLVER_URL.startswith("https://doi.org/")
