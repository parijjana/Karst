"""Index contracts and services owned by the Karst data core."""

from src.karst_core.indexing.identity import (
    FileCandidate,
    ParsedSymbol,
    SourceSnapshot,
    derive_file_stable_id,
    derive_symbol_stable_id,
)
from src.karst_core.indexing.models import IndexResult

__all__ = [
    "FileCandidate",
    "IndexResult",
    "IncrementalIndexService",
    "ParsedSymbol",
    "ProjectIndexService",
    "SourceSnapshot",
    "derive_file_stable_id",
    "derive_symbol_stable_id",
]


def __getattr__(name: str) -> object:
    """Load service facades lazily so parser contracts can import index models."""
    if name == "IncrementalIndexService":
        from src.karst_core.indexing.generation_service import IncrementalIndexService

        return IncrementalIndexService
    if name == "ProjectIndexService":
        from src.karst_core.indexing.service import ProjectIndexService

        return ProjectIndexService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
