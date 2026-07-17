"""Compatibility facade for core generation indexing."""

from src.karst_core.indexing.generation_service import (
    IncrementalIndexService as IncrementalIndexService,
)

__all__ = ("IncrementalIndexService",)
