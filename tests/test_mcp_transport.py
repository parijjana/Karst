from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import TextContent

from tests.database_v2_generation_support import create_v2_database


def test_stdio_transport_lists_and_calls_tools_without_stdout_corruption(
    tmp_path: Path,
) -> None:
    async def exercise_transport() -> tuple[set[str], str]:
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
                tools = await session.list_tools()
                result = await session.call_tool(
                    "query_symbol",
                    arguments={"project_name": "missing", "symbol_name": "x"},
                )
                content = result.content[0]
                assert isinstance(content, TextContent)
                return {tool.name for tool in tools.tools}, content.text

    names, response = asyncio.run(exercise_transport())

    assert "index_project" in names
    assert "query_symbol" in names
    assert response == "Project not found."


def test_stdio_transport_reports_legacy_database_recovery_without_deleting_data(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "data" / "karst.db"
    database_path.parent.mkdir()
    create_v2_database(
        database_path,
        project_root="legacy/project",
        file_path="legacy/project/a.py",
    )

    async def call_query() -> tuple[bool, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "KARST_DATA_DIR": str(tmp_path / "data"),
                "KARST_DB_PATH": str(database_path),
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
                result = await session.call_tool(
                    "query_symbol",
                    arguments={"project_name": "missing", "symbol_name": "x"},
                )
                content = result.content[0]
                assert isinstance(content, TextContent)
                return bool(result.isError), content.text

    is_error, message = asyncio.run(call_query())

    assert is_error
    assert "No data was deleted" in message
    assert "rebuild_database(confirmation='DELETE_AND_REBUILD')" in message
    assert database_path.exists()
