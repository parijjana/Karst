from __future__ import annotations

from collections.abc import Iterable

from mcp.server.fastmcp import FastMCP

from src.karst_mcp.contracts import ToolContract


def create_server(name: str, contracts: Iterable[ToolContract]) -> FastMCP:
    """Create a FastMCP server from explicit transport-neutral contracts."""
    server = FastMCP(name)
    for contract in contracts:
        server.add_tool(contract.handler, name=contract.name)
    return server
