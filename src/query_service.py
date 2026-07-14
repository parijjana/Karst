from __future__ import annotations

from src.query_cursor import InvalidCursorError, StaleCursorError, SymbolFilters
from src.query_models import (
    ApiError,
    QueryErrorCode,
    SnippetError,
    SnippetSuccess,
    SymbolPageError,
    SymbolPageSuccess,
)
from src.symbol_repository import QueryRepositoryError, SymbolRepository


class QueryService:
    """Stable API boundary for read-only queries over the active generation."""

    def __init__(self, repository: SymbolRepository) -> None:
        self.repository = repository

    @staticmethod
    def _error(code: QueryErrorCode, message: str, *, retryable: bool = False) -> ApiError:
        return ApiError(code=code, message=message, retryable=retryable)

    def list_symbols(
        self, project_id: int, filters: SymbolFilters, limit: int, cursor: str | None = None
    ) -> SymbolPageSuccess | SymbolPageError:
        try:
            page = self.repository.list_symbols(project_id, filters, limit, cursor)
        except InvalidCursorError:
            return SymbolPageError(error=self._error(QueryErrorCode.INVALID_CURSOR, "Cursor is invalid."))
        except StaleCursorError:
            return SymbolPageError(error=self._error(QueryErrorCode.STALE_CURSOR, "Cursor is stale."))
        except QueryRepositoryError as error:
            message = str(error)
            if message == "Index is not ready.":
                return SymbolPageError(error=self._error(QueryErrorCode.INDEX_NOT_READY, message, retryable=True))
            if message == "Limit is invalid.":
                return SymbolPageError(error=self._error(QueryErrorCode.LIMIT_EXCEEDED, message))
            return SymbolPageError(error=self._error(QueryErrorCode.INDEX_NOT_READY, "Query is unavailable.", retryable=True))
        except (TypeError, ValueError):
            return SymbolPageError(error=self._error(QueryErrorCode.LIMIT_EXCEEDED, "Query parameters are invalid."))
        return SymbolPageSuccess(data=page)

    def get_snippet(self, project_id: int, stable_symbol_id: str) -> SnippetSuccess | SnippetError:
        try:
            snippet = self.repository.get_snippet(project_id, stable_symbol_id)
        except QueryRepositoryError as error:
            message = str(error)
            if message == "Symbol not found.":
                return SnippetError(error=self._error(QueryErrorCode.SYMBOL_NOT_FOUND, message))
            if message == "Source is stale.":
                return SnippetError(error=self._error(QueryErrorCode.SOURCE_STALE, message, retryable=True))
            if message == "Index is not ready.":
                return SnippetError(error=self._error(QueryErrorCode.INDEX_NOT_READY, message, retryable=True))
            return SnippetError(error=self._error(QueryErrorCode.SOURCE_STALE, "Source is unavailable.", retryable=True))
        except (TypeError, ValueError):
            return SnippetError(error=self._error(QueryErrorCode.SYMBOL_NOT_FOUND, "Symbol is invalid."))
        return SnippetSuccess(data=snippet)


__all__ = ["QueryService"]
