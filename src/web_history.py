from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from src.web_auth import request_settings
from src.web_data import get_db, page_bounds, table_exists


router = APIRouter()


@router.get("/api/projects/{project_id}/commits")
async def get_project_commits(
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
        if not table_exists(cursor, "commits"):
            return []
        rows = cursor.execute(
            """
            SELECT c.id, c.commit_hash, c.message, c.timestamp,
                   GROUP_CONCAT(cf.status || ':' || cf.file_path, ', ') AS files_changed
            FROM commits c
            LEFT JOIN commit_files cf ON c.id = cf.commit_id
            WHERE c.project_id = ?
            GROUP BY c.id
            ORDER BY c.timestamp DESC, c.id DESC LIMIT ? OFFSET ?
            """,
            (project_id, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]


@router.get("/api/telemetry")
async def get_telemetry(
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
            SELECT strftime('%Y-%m-%d %H:00', timestamp) AS time_bucket,
                   tool_name, COUNT(*) AS calls, AVG(latency_ms) AS avg_latency,
                   SUM(tokens_saved) AS total_tokens
            FROM telemetry
            GROUP BY time_bucket, tool_name
            ORDER BY time_bucket DESC, tool_name ASC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]


@router.get("/api/services/metrics")
async def get_service_metrics(
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
            SELECT id, tool_name AS service, latency_ms,
                   tokens_saved AS processed_count, details, timestamp
            FROM telemetry
            WHERE tool_name LIKE 'service:%'
            ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]
