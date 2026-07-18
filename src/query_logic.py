from __future__ import annotations

from typing import Any

from src.karst_core.embeddings import (
    MAX_SEMANTIC_RESULTS,
    MODEL_NAME,
    MODEL_REVISION,
    cosine_similarity,
    create_embed_model,
)
from src.karst_core.embeddings import do_semantic_search as _do_semantic_search
from src.karst_core.query.dependencies import do_find_deps


__all__ = [
    "MAX_SEMANTIC_RESULTS",
    "SEMANTIC_MODEL_NAME",
    "SEMANTIC_MODEL_REVISION",
    "cosine_similarity",
    "do_find_deps",
    "do_semantic_search",
    "get_embed_model",
]


SEMANTIC_MODEL_NAME = MODEL_NAME
SEMANTIC_MODEL_REVISION = MODEL_REVISION
_embed_model: Any | None = None


def get_embed_model() -> Any:
    global _embed_model
    if _embed_model is None:
        _embed_model = create_embed_model()
    return _embed_model


def do_semantic_search(
    db: Any, project_id: int, query: str, limit: int = 5
) -> tuple[str, float, int]:
    return _do_semantic_search(
        db,
        project_id,
        query,
        limit,
        model_provider=get_embed_model,
    )
