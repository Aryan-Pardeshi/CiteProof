
import httpx
import pytest

from backend.app.tools.oa_resolver import (
    resolve_core,
    resolve_europe_pmc,
    resolve_oa_pdf_url,
    resolve_unpaywall,
)


@pytest.mark.asyncio
async def test_resolve_unpaywall_missing_doi():
    async with httpx.AsyncClient() as client:
        url = await resolve_unpaywall(client, {"title": "Test Paper"})
        assert url == ""

@pytest.mark.asyncio
async def test_resolve_unpaywall_unset_email(monkeypatch):
    monkeypatch.delenv("OPENALEX_EMAIL", raising=False)
    async with httpx.AsyncClient() as client:
        url = await resolve_unpaywall(client, {"doi": "10.1038/s41586-020-2649-2"})
        assert url == ""

@pytest.mark.asyncio
async def test_resolve_europe_pmc_no_doi():
    async with httpx.AsyncClient() as client:
        url = await resolve_europe_pmc(client, {"title": "No DOI Paper"})
        assert url == ""

@pytest.mark.asyncio
async def test_resolve_core_unset_key(monkeypatch):
    monkeypatch.delenv("CORE_API_KEY", raising=False)
    async with httpx.AsyncClient() as client:
        url = await resolve_core(client, {"doi": "10.1016/j.cell.2020.08.001"})
        assert url == ""

@pytest.mark.asyncio
async def test_resolve_oa_pdf_url_existing_url():
    async with httpx.AsyncClient() as client:
        paper = {"title": "Test", "pdf_url": "https://example.com/paper.pdf"}
        url = await resolve_oa_pdf_url(client, paper)
        assert url == "https://example.com/paper.pdf"
