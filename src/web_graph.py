from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from src.karst_core.query import (
    SelectedFolderError,
    StructuralGraphPayload,
    StructuralGraphService,
)
from src.web_auth import request_settings


router = APIRouter()


@router.get("/api/graph")
async def get_graph(
    request: Request,
    project_id: int | None = None,
    selected_folder_id: str | None = None,
) -> StructuralGraphPayload:
    configured = request_settings(request)
    try:
        return (
            StructuralGraphService(configured.db_path)
            .graph(project_id, selected_folder_id)
            .as_dict()
        )
    except SelectedFolderError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        ) from error
