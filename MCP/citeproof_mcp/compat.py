# SPDX-License-Identifier: MIT
"""One import point for the server class.

mcp 2.x calls it ``MCPServer``; mcp 1.x (shipped inside Claude Science's own tool
environment) calls the same class ``FastMCP``. Both accept ``@server.tool()``.
"""

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer

__all__ = ["MCPServer"]
