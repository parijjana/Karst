from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from src.karst_core.query import OperationalReadService
from src.web_auth import request_settings
from src.web_data import page_bounds


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
    return OperationalReadService(configured.db_path).commits(project_id, limit, offset)


@router.get("/api/telemetry")
async def get_telemetry(
    request: Request,
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> list[dict[str, object]]:
    configured = request_settings(request)
    limit, offset = page_bounds(configured, limit, offset)
    return OperationalReadService(configured.db_path).telemetry_aggregates(
        limit, offset
    )


@router.get("/api/services/metrics")
async def get_service_metrics(
    request: Request,
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> list[dict[str, object]]:
    configured = request_settings(request)
    limit, offset = page_bounds(configured, limit, offset)
    return OperationalReadService(configured.db_path).service_metrics(limit, offset)
