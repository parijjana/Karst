from __future__ import annotations

from collections.abc import Iterable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from src.karst_mcp.contracts import ToolContract


def create_server(name: str, contracts: Iterable[ToolContract]) -> FastMCP:
    """Create a FastMCP server from explicit transport-neutral contracts."""
    server = FastMCP(name)
    for contract in contracts:
        policy = contract.annotations
        server.add_tool(
            contract.handler,
            name=contract.name,
            annotations=ToolAnnotations(
                readOnlyHint=policy.read_only,
                destructiveHint=policy.destructive,
                idempotentHint=policy.idempotent,
                openWorldHint=policy.open_world,
            ),
        )
    return server
