"""Compatibility facade for core project indexing."""

from src.karst_core.indexing.service import (
    IGNORED_DIRECTORIES as IGNORED_DIRECTORIES,
    SUPPORTED_EXTENSIONS as SUPPORTED_EXTENSIONS,
    IndexResult as IndexResult,
    ProjectIndexService as ProjectIndexService,
)

__all__ = (
    "IGNORED_DIRECTORIES",
    "SUPPORTED_EXTENSIONS",
    "IndexResult",
    "ProjectIndexService",
)
