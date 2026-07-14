from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from src.query_cursor import (
    InvalidCursorError,
    StaleCursorError,
    SymbolFilters,
    SymbolPageCursorCodec,
    SymbolPageKeyset,
)
from src.query_models import Snippet, SymbolPage, SymbolRef, MAX_PAGE_ITEMS, MAX_SNIPPET_UTF8_BYTES


class QueryRepositoryError(ValueError):
    """A safe, client-facing query failure."""


class SymbolRepository:
    """Read-only symbol and source access against an immutable ready generation."""

    def __init__(self, database: Any, cursor_codec: SymbolPageCursorCodec,
                 *, snippet_utf8_budget: int = MAX_SNIPPET_UTF8_BYTES,
                 max_source_bytes: int = 4 * 1024 * 1024) -> None:
        if not isinstance(snippet_utf8_budget, int) or not 1 <= snippet_utf8_budget <= MAX_SNIPPET_UTF8_BYTES:
            raise ValueError("snippet_utf8_budget is invalid.")
        if not isinstance(max_source_bytes, int) or max_source_bytes < 1:
            raise ValueError("max_source_bytes is invalid.")
        self.database = database
        self.codec = cursor_codec
        self.snippet_utf8_budget = snippet_utf8_budget
        self.max_source_bytes = max_source_bytes

    def _generation(self, project_id: int) -> tuple[int, str, Path]:
        row = self.database.conn.execute(
            "SELECT generation.id, project.stable_id, project.path "
            "FROM index_generations AS generation JOIN projects AS project "
            "ON project.id = generation.project_id WHERE generation.project_id = ? "
            "AND generation.status = 'active' AND generation.query_ready = 1",
            (project_id,),
        ).fetchone()
        if row is None:
            raise QueryRepositoryError("Index is not ready.")
        return int(row[0]), str(row[1]), Path(str(row[2])).resolve(strict=True)

    def list_symbols(
        self, project_id: int, filters: SymbolFilters, limit: int, cursor: str | None = None
    ) -> SymbolPage:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE_ITEMS:
            raise QueryRepositoryError("Limit is invalid.")
        generation, project_stable_id, _ = self._generation(project_id)
        after: SymbolPageKeyset | None = None
        if cursor is not None:
            try:
                after = self.codec.decode(cursor, expected_project_stable_id=project_stable_id,
                    expected_generation_id=generation, filters=filters, limit=limit)
            except (InvalidCursorError, StaleCursorError):
                raise
        clauses = ["node.project_id = ?", "node.generation_id = ?"]
        params: list[object] = [project_id, generation]
        for column, value in (("node.type", filters.kind), ("node.name", filters.name),
                              ("node.qualified_name", filters.qualified_name),
                              ("file.relative_path", filters.relative_path)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if after is not None:
            clauses.append("(file.relative_path, node.start_line, node.qualified_name, node.stable_id) > (?, ?, ?, ?)")
            params.extend(after.as_list())
        rows = self.database.conn.execute(
            "SELECT node.stable_id, node.type, node.name, node.qualified_name, node.signature, "
            "file.relative_path, node.start_line, node.end_line, file.hash "
            "FROM nodes AS node JOIN files AS file ON file.project_id=node.project_id "
            "AND file.generation_id=node.generation_id AND file.id=node.file_id WHERE "
            + " AND ".join(clauses)
            + " ORDER BY file.relative_path, node.start_line, node.qualified_name, node.stable_id LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
        items = tuple(SymbolRef(stable_symbol_id=str(r[0]), kind=str(r[1]), name=str(r[2]),
            qualified_name=str(r[3]), signature=None if r[4] is None else str(r[4]),
            relative_path=str(r[5]), start_line=int(r[6]), end_line=int(r[7]),
            source_sha256=str(r[8]), generation_id=generation) for r in rows[:limit])
        next_cursor = None
        if len(rows) > limit:
            last = items[-1]
            next_cursor = self.codec.encode(project_stable_id=project_stable_id,
                generation_id=generation, filters=filters, limit=limit,
                after=SymbolPageKeyset(last.relative_path, last.start_line,
                    last.qualified_name, last.stable_symbol_id))
        return SymbolPage(items=items, next_cursor=next_cursor, generation_id=generation)

    def get_snippet(self, project_id: int, stable_symbol_id: str) -> Snippet:
        generation, _, root = self._generation(project_id)
        row = self.database.conn.execute(
            "SELECT node.start_line, node.end_line, file.relative_path, file.path, file.hash "
            "FROM nodes AS node JOIN files AS file ON file.project_id=node.project_id "
            "AND file.generation_id=node.generation_id AND file.id=node.file_id WHERE "
            "node.project_id = ? AND node.generation_id = ? AND node.stable_id = ?",
            (project_id, generation, stable_symbol_id),
        ).fetchone()
        if row is None:
            raise QueryRepositoryError("Symbol not found.")
        relative, raw_path, expected_hash = str(row[2]), Path(str(row[3])), str(row[4])
        try:
            path = (root / relative).resolve(strict=True)
        except (OSError, ValueError) as error:
            raise QueryRepositoryError("Source is stale.") from error
        if path != root / relative or path.is_symlink() or not path.is_file():
            raise QueryRepositoryError("Source is stale.")
        try:
            data = path.read_bytes()
            if len(data) > self.max_source_bytes:
                raise QueryRepositoryError("Source is stale.")
            actual_hash = hashlib.sha256(data).hexdigest()
            text = data.decode("utf-8", errors="strict")
        except (OSError, UnicodeError) as error:
            raise QueryRepositoryError("Source is stale.") from error
        try:
            recorded_path = raw_path.resolve(strict=False)
        except (OSError, ValueError) as error:
            raise QueryRepositoryError("Source is stale.") from error
        if actual_hash != expected_hash or recorded_path != path:
            raise QueryRepositoryError("Source is stale.")
        lines = text.splitlines()
        start, end = int(row[0]), int(row[1])
        if start > len(lines) or end > len(lines):
            raise QueryRepositoryError("Source is stale.")
        text = "\n".join(lines[start - 1:end])
        encoded = text.encode("utf-8")
        truncated = len(encoded) > self.snippet_utf8_budget
        if truncated:
            encoded = encoded[: self.snippet_utf8_budget]
            while True:
                try:
                    text = encoded.decode("utf-8")
                    break
                except UnicodeDecodeError:
                    encoded = encoded[:-1]
        return Snippet(text=text, start_line=start, end_line=end,
            truncated=truncated, source_sha256=actual_hash)
