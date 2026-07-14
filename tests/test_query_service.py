from __future__ import annotations

from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from src.query_cursor import InvalidCursorError, StaleCursorError, SymbolFilters
from src.query_models import QueryErrorCode, Snippet, SymbolPage, SymbolRef
from src.query_service import QueryService
from src.symbol_repository import QueryRepositoryError


def _service(method, result=None):
    repo = SimpleNamespace()
    setattr(repo, method, lambda *args: result)
    return QueryService(repo)


def _symbol() -> SymbolRef:
    return SymbolRef(
        stable_symbol_id=str(uuid5(NAMESPACE_URL, "symbol")), kind="function",
        name="f", qualified_name="m.f", signature="f()", relative_path="a.py",
        start_line=1, end_line=1, source_sha256="a" * 64, generation_id=1,
    )


def test_list_symbols_maps_cursor_and_repository_errors():
    filters = SymbolFilters()
    for error, code in ((InvalidCursorError("bad"), QueryErrorCode.INVALID_CURSOR),
                        (StaleCursorError("old"), QueryErrorCode.STALE_CURSOR),
                        (QueryRepositoryError("Index is not ready."), QueryErrorCode.INDEX_NOT_READY),
                        (QueryRepositoryError("Limit is invalid."), QueryErrorCode.LIMIT_EXCEEDED)):
        repo = SimpleNamespace(list_symbols=lambda *args, error=error: (_ for _ in ()).throw(error))
        response = QueryService(repo).list_symbols(1, filters, 10)
        assert response.status == "error" and response.error.code == code


def test_list_symbols_success_and_invalid_parameters():
    page = SymbolPage(items=(_symbol(),), generation_id=1)
    response = _service("list_symbols", page).list_symbols(1, SymbolFilters(), 1)
    assert response.status == "success" and response.data == page
    repo = SimpleNamespace(list_symbols=lambda *args: (_ for _ in ()).throw(ValueError("bad")))
    response = QueryService(repo).list_symbols(1, SymbolFilters(), 1)
    assert response.error.code == QueryErrorCode.LIMIT_EXCEEDED


def test_snippet_maps_not_ready_not_found_stale_and_success():
    snippet = Snippet(text="line", start_line=1, end_line=1, truncated=False, source_sha256="b" * 64)
    assert _service("get_snippet", snippet).get_snippet(1, "x").status == "success"
    for message, code in (("Symbol not found.", QueryErrorCode.SYMBOL_NOT_FOUND),
                          ("Source is stale.", QueryErrorCode.SOURCE_STALE),
                          ("Index is not ready.", QueryErrorCode.INDEX_NOT_READY)):
        repo = SimpleNamespace(get_snippet=lambda *args, message=message: (_ for _ in ()).throw(QueryRepositoryError(message)))
        response = QueryService(repo).get_snippet(1, "x")
        assert response.status == "error" and response.error.code == code

