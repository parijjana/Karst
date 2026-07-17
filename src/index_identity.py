"""Compatibility facade for core indexing identities."""

from uuid import uuid5 as uuid5

from src.karst_core.indexing.identity import (
    FileCandidate as FileCandidate,
    ParsedSymbol as ParsedSymbol,
    SourceSnapshot as SourceSnapshot,
    _require_positive_line as _require_positive_line,
    _require_relative_posix_path as _require_relative_posix_path,
    _require_uuid5 as _require_uuid5,
    derive_file_stable_id as derive_file_stable_id,
    derive_symbol_stable_id as derive_symbol_stable_id,
)

__all__ = (
    "FileCandidate",
    "ParsedSymbol",
    "SourceSnapshot",
    "derive_file_stable_id",
    "derive_symbol_stable_id",
)
