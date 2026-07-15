from __future__ import annotations

from fastapi import APIRouter, Request

from src.karst_core.structural_graph import StructuralGraphService
from src.web_auth import request_settings


router = APIRouter()


@router.get("/api/graph")
async def get_graph(
    request: Request, project_id: int | None = None
) -> dict[str, list[dict[str, object]]]:
    configured = request_settings(request)
    return StructuralGraphService(configured.db_path).graph(project_id).as_dict()
