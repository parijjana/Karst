from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import sys

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
import pytest

from src import main


EXPECTED_ANNOTATIONS = {
    "list_symbols": (False, False, True, False),
    "index_project": (False, True, False, True),
    "update_graph": (False, True, False, True),
    "rebuild_database": (False, True, False, False),
    "query_symbol": (False, False, False, True),
    "get_file_outline": (False, False, False, True),
    "find_dependencies": (False, False, False, True),
    "find_dependents": (False, False, False, True),
    "log_commit": (False, True, False, False),
    "backfill_git_history": (False, True, False, True),
    "semantic_search": (False, False, False, False),
}


def _registered_annotations() -> dict[str, tuple[bool | None, ...]]:
    return {
        name: (
            tool.annotations.readOnlyHint,
            tool.annotations.destructiveHint,
            tool.annotations.idempotentHint,
            tool.annotations.openWorldHint,
        )
        for name, tool in main.mcp._tool_manager._tools.items()
        if tool.annotations is not None
    }


def test_exact_public_tool_surface_has_conservative_annotations() -> None:
    assert _registered_annotations() == EXPECTED_ANNOTATIONS


def test_transport_contract_annotation_policy_is_immutable() -> None:
    contract = main.TOOL_CONTRACTS[0]

    with pytest.raises(FrozenInstanceError):
        contract.annotations.read_only = False  # type: ignore[misc]


def test_list_tools_serializes_exact_annotations(tmp_path: Path) -> None:
    async def list_annotations() -> dict[str, tuple[bool | None, ...]]:
        environment = os.environ.copy()
        environment.update(
            {
                "KARST_DATA_DIR": str(tmp_path / "data"),
                "KARST_DB_PATH": str(tmp_path / "data" / "karst.db"),
                "KARST_ALLOWED_ROOTS": str(tmp_path),
            }
        )
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "src.main"],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                response = await session.list_tools()
                return {
                    tool.name: (
                        tool.annotations.readOnlyHint,
                        tool.annotations.destructiveHint,
                        tool.annotations.idempotentHint,
                        tool.annotations.openWorldHint,
                    )
                    for tool in response.tools
                    if tool.annotations is not None
                }

    assert asyncio.run(list_annotations()) == EXPECTED_ANNOTATIONS
