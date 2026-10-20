# SPDX-License-Identifier: MIT
"""Shared fixtures: every test gets its own empty CITEPROOF_MCP_HOME and an in-memory client."""

from __future__ import annotations

import json

import pytest
from citeproof_mcp.server import build_server
from mcp.client import Client


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CITEPROOF_MCP_HOME", str(tmp_path / "mcp-home"))
    return tmp_path / "mcp-home"


async def call(name, **arguments):
    """Call one tool on a fresh in-memory server and return its JSON result as a dict.

    State lives on disk under the isolated CITEPROOF_MCP_HOME, so a new server per call
    is fine. A fixture holding one client open breaks anyio's cancel scope across tasks.
    """
    async with Client(build_server()) as c:
        result = await c.call_tool(name, arguments)
    assert not result.is_error, result
    return json.loads(result.content[0].text)
