"""Compatibility facade for Karst core query response contracts."""

from src.karst_core.query.models import (
    MAX_CURSOR_UTF8_BYTES,
    MAX_ERROR_MESSAGE_UTF8_BYTES,
    MAX_PAGE_ITEMS,
    MAX_SNIPPET_UTF8_BYTES,
    ApiError,
    QueryErrorCode,
    Snippet,
    SnippetEnvelope,
    SnippetError,
    SnippetSuccess,
    SymbolPage,
    SymbolPageEnvelope,
    SymbolPageError,
    SymbolPageSuccess,
    SymbolRef,
)

__all__ = [
    "MAX_CURSOR_UTF8_BYTES",
    "MAX_ERROR_MESSAGE_UTF8_BYTES",
    "MAX_PAGE_ITEMS",
    "MAX_SNIPPET_UTF8_BYTES",
    "ApiError",
    "QueryErrorCode",
    "Snippet",
    "SnippetEnvelope",
    "SnippetError",
    "SnippetSuccess",
    "SymbolPage",
    "SymbolPageEnvelope",
    "SymbolPageError",
    "SymbolPageSuccess",
    "SymbolRef",
]
