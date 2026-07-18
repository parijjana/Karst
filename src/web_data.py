from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status

from src.settings import Settings
from src.karst_core.query import OperationalReadService, ProjectSummaryService
from src.web_auth import request_settings


router = APIRouter()


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
    return OperationalReadService(configured.db_path).stats()


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
    return OperationalReadService(configured.db_path).nodes(project_id, limit, offset)


@router.get("/api/projects/{project_id}/telemetry")
async def get_project_telemetry(
    project_id: int,
    request: Request,
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> list[dict[str, object]]:
    configured = request_settings(request)
    limit, offset = page_bounds(configured, limit, offset)
    return OperationalReadService(configured.db_path).project_telemetry(
        project_id, limit, offset
    )
