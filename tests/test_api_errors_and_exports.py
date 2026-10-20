"""API error responses and paper exports.

Exports must not put the thread id in a filesystem path and must not leave files
behind; failures must not echo exception text to the client.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import research_mode

SECRET = "sk-secret-value-from-a-stack-trace"


class _FakeGraph:
    def __init__(self, values):
        self._values = values

    async def aget_state(self, config):
        return SimpleNamespace(values=self._values, next=())


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(research_mode, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(research_mode, "get_research_mode_graph", lambda: _FakeGraph({"title": "A paper"}))
    app = FastAPI()
    app.include_router(research_mode.router)
    return TestClient(app)


def _fake_writer(content: bytes):
    def write(state, output_path):
        with open(output_path, "wb") as f:
            f.write(content)
        return output_path

    return write


@pytest.mark.parametrize(
    "route, generator, suffix",
    [
        ("/research-mode/export/abc_123", "generate_paper_pdf", ".pdf"),
        ("/research-mode/export/docx/abc_123", "generate_paper_docx", ".docx"),
    ],
)
def test_export_sends_file_and_deletes_it(client, tmp_path, monkeypatch, route, generator, suffix):
    monkeypatch.setattr(research_mode, generator, _fake_writer(b"paper-bytes"))

    response = client.post(route)

    assert response.status_code == 200
    assert response.content == b"paper-bytes"
    assert f'filename="research_paper_abc_123{suffix}"' in response.headers["content-disposition"]
    assert list((tmp_path / "exports").iterdir()) == []


def test_export_file_name_does_not_use_thread_id(client, monkeypatch):
    seen = []

    def write(state, output_path):
        seen.append(output_path)
        with open(output_path, "wb") as f:
            f.write(b"x")

    monkeypatch.setattr(research_mode, "generate_paper_pdf", write)

    assert client.post("/research-mode/export/abc_123").status_code == 200
    assert "abc_123" not in seen[0]


@pytest.mark.parametrize(
    "route, generator",
    [
        ("/research-mode/export/abc_123", "generate_paper_pdf"),
        ("/research-mode/export/docx/abc_123", "generate_paper_docx"),
    ],
)
def test_export_failure_hides_exception_text_and_cleans_up(client, tmp_path, monkeypatch, route, generator):
    def boom(state, output_path):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(research_mode, generator, boom)

    response = client.post(route)

    assert response.status_code == 500
    assert SECRET not in response.text
    assert "Check the server logs" in response.json()["detail"]
    assert list((tmp_path / "exports").iterdir()) == []


def test_export_rejects_invalid_thread_id(client):
    assert client.post("/research-mode/export/bad.id").status_code == 400


def test_start_research_mode_failure_hides_exception_text(client, monkeypatch):
    class _BrokenGraph:
        async def astream(self, *args, **kwargs):
            raise RuntimeError(SECRET)
            yield  # makes this an async generator

    monkeypatch.setattr(research_mode, "get_research_mode_graph", lambda: _BrokenGraph())

    response = client.post("/research-mode/start", json={"problem_statement": "A valid research question?"})

    body = response.json()
    assert body["status"] == "error"
    assert SECRET not in response.text
    assert "Check the server logs" in body["error"]
