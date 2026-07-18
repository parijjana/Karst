from __future__ import annotations

from typing import Any


MODEL_NAME = "BAAI/bge-small-en-v1.5"
MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
_embed_model: Any | None = None


def create_embed_model() -> Any:
    """Load the pinned embedding model from the local artifact cache only."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(
        MODEL_NAME,
        revision=MODEL_REVISION,
        local_files_only=True,
        trust_remote_code=False,
    )


def get_embed_model() -> Any:
    """Reuse one locally loaded model for read-only semantic queries."""
    global _embed_model
    if _embed_model is None:
        _embed_model = create_embed_model()
    return _embed_model
