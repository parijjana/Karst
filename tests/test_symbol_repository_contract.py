from __future__ import annotations

import pytest

from src.query_cursor import SymbolFilters, SymbolPageCursorCodec
from src.symbol_repository import QueryRepositoryError, SymbolRepository


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []


class _Connection:
    def __init__(self, row=None):
        self.rows = [row]

    def execute(self, _sql, _params=()):
        return _Cursor(self.rows.pop(0) if self.rows else None)


class _Database:
    def __init__(self, row=None):
        self.conn = _Connection(row)


def repository(row=None) -> SymbolRepository:
    return SymbolRepository(_Database(row), SymbolPageCursorCodec(b"k" * 32))


@pytest.mark.parametrize("kwargs", [{"snippet_utf8_budget": 0}, {"max_source_bytes": 0}])
def test_source_read_budgets_are_positive(kwargs) -> None:
    with pytest.raises(ValueError):
        SymbolRepository(_Database(), SymbolPageCursorCodec(b"k" * 32), **kwargs)


def test_unready_generation_fails_closed_without_querying_symbols() -> None:
    with pytest.raises(QueryRepositoryError, match="not ready"):
        repository().list_symbols(1, SymbolFilters(), 1)


@pytest.mark.parametrize("limit", [0, 201, True, "1"])
def test_symbol_page_limit_is_strictly_bounded(limit: object) -> None:
    with pytest.raises(QueryRepositoryError, match="Limit is invalid"):
        repository().list_symbols(1, SymbolFilters(), limit)  # type: ignore[arg-type]


def test_missing_snippet_symbol_is_safe_not_found(tmp_path) -> None:
    ready = (7, "00000000-0000-5000-8000-000000000000", str(tmp_path))
    database = _Database(ready)
    database.conn.rows.append(None)
    with pytest.raises(QueryRepositoryError, match="Symbol not found"):
        SymbolRepository(database, SymbolPageCursorCodec(b"k" * 32)).get_snippet(1, "x")
