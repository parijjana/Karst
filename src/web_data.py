from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Iterator

from fastapi import APIRouter, HTTPException, Query, Request, status

from src.settings import Settings
from src.karst_core.query import ProjectSummaryService
from src.web_auth import request_settings


router = APIRouter()


@contextmanager
def get_db(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def table_exists(cursor: sqlite3.Cursor, table_name: str) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cursor.fetchone() is not None


def page_bounds(
    configured: Settings, limit: int | None, offset: int
) -> tuple[int, int]:
    selected = configured.dashboard_default_page_size if limit is None else limit
    if selected > configured.dashboard_max_page_size:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Requested page exceeds the dashboard limit.",
        )
    return selected, offset


@router.get("/api/stats")
async def get_stats(request: Request) -> dict[str, int]:
    configured = request_settings(request)
    if not configured.db_path.exists():
        return {
            "total_projects": 0,
            "total_nodes": 0,
            "queries_served": 0,
            "tokens_saved": 0,
        }
    with get_db(configured.db_path) as connection:
        cursor = connection.cursor()
        projects = cursor.execute("SELECT COUNT(*) AS c FROM projects").fetchone()["c"]
        nodes = cursor.execute("SELECT COUNT(*) AS c FROM nodes").fetchone()["c"]
        queries = 0
        tokens = 0
        if table_exists(cursor, "telemetry"):
            row = cursor.execute(
                "SELECT COUNT(*) AS c, SUM(tokens_saved) AS t FROM telemetry"
            ).fetchone()
            if row:
                queries = row["c"] or 0
                tokens = row["t"] or 0
        return {
            "total_projects": projects,
            "total_nodes": nodes,
            "queries_served": queries,
            "tokens_saved": tokens,
        }


@router.get("/api/projects")
async def get_projects(
    request: Request,
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> list[dict[str, object]]:
    configured = request_settings(request)
    limit, offset = page_bounds(configured, limit, offset)
    return ProjectSummaryService(configured.db_path).projects(limit, offset)


@router.get("/api/projects/{project_id}/files")
async def get_project_files(
    project_id: int,
    request: Request,
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> list[dict[str, object]]:
    configured = request_settings(request)
    limit, offset = page_bounds(configured, limit, offset)
    return ProjectSummaryService(configured.db_path).files(project_id, limit, offset)


@router.get("/api/projects/{project_id}/nodes")
async def get_project_nodes(
    project_id: int,
    request: Request,
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> list[dict[str, object]]:
    configured = request_settings(request)
    limit, offset = page_bounds(configured, limit, offset)
    if not configured.db_path.exists():
        return []
    with get_db(configured.db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, file_id, type, name, start_line, end_line FROM nodes
            WHERE project_id = ? ORDER BY id LIMIT ? OFFSET ?
            """,
            (project_id, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]


@router.get("/api/projects/{project_id}/telemetry")
async def get_project_telemetry(
    project_id: int,
    request: Request,
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> list[dict[str, object]]:
    configured = request_settings(request)
    limit, offset = page_bounds(configured, limit, offset)
    if not configured.db_path.exists():
        return []
    with get_db(configured.db_path) as connection:
        cursor = connection.cursor()
        if not table_exists(cursor, "telemetry"):
            return []
        rows = cursor.execute(
            """
            SELECT id, tool_name, latency_ms, tokens_saved, timestamp FROM telemetry
            WHERE project_id = ? ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?
            """,
            (project_id, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]
