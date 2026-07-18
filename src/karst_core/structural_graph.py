"""Compatibility facade for the Karst core structural-graph query interface."""

from src.karst_core.query.structural_graph import (
    SelectedFolderError,
    StructuralGraph,
    StructuralGraphPayload,
    StructuralGraphService,
)

__all__ = [
    "SelectedFolderError",
    "StructuralGraph",
    "StructuralGraphPayload",
    "StructuralGraphService",
]
