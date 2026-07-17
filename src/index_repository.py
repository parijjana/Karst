"""Compatibility facade for core index persistence."""

from src.karst_core.indexing.repository import (
    Generation as Generation,
    GenerationRepository as GenerationRepository,
    IndexRepository as IndexRepository,
)

__all__ = ("Generation", "GenerationRepository", "IndexRepository")
