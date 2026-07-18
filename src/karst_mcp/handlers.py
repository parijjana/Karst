from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import secrets
from typing import Any, Generic, Protocol, TypeVar

from src.karst_core.database.database import Database
from src.karst_core.database.database_session import get_project_id
from src.karst_core.query import (
    ApiError,
    QueryErrorCode,
    QueryService,
    SymbolFilters,
    SymbolPageCursorCodec,
    SymbolPageError,
    SymbolRepository,
    do_find_deps,
)


HistoryOperation = Callable[[Any, int, str, str, int], str]
SemanticOperation = Callable[[Any, int, str, int], tuple[str, float, int]]
DatabaseFactory = Callable[[], Database]


class CoreConfiguration(Protocol):
    """Configuration fields consumed directly by the MCP adapter."""

    @property
    def data_dir(self) -> Path: ...

    @property
    def db_path(self) -> Path: ...


ConfigurationT = TypeVar("ConfigurationT", bound=CoreConfiguration)


class IndexService(Protocol):
    def index_project(self, project_name: str, root_path: str) -> str: ...

    def update_graph(self, project_name: str, filepaths: list[str]) -> str: ...


class ToolService(Protocol):
    def query_symbol(self, project_name: str, symbol_name: str) -> str: ...

    def get_file_outline(self, project_name: str, file_path: str) -> str: ...

    def dependency_query(
        self,
        project_name: str,
        symbol_name: str,
        reverse: bool,
        operation: Callable[[Any, int, str, bool], tuple[str, float, int]],
    ) -> str: ...

    def log_commit(
        self,
        project_name: str,
        commit_hash: str,
        message: str,
        files_changed: list[dict],
    ) -> str: ...

    def backfill(
        self,
        project_name: str,
        limit: int,
        operation: HistoryOperation,
    ) -> str: ...

    def semantic_search(
        self,
        project_name: str,
        query: str,
        limit: int,
        operation: SemanticOperation,
    ) -> str: ...


class KarstToolHandlers(Generic[ConfigurationT]):
    """Own Karst's MCP-facing application orchestration."""

    def __init__(
        self,
        configuration_provider: Callable[[], ConfigurationT],
        index_service_factory: Callable[
            [ConfigurationT, DatabaseFactory], IndexService
        ],
        tool_service_factory: Callable[[ConfigurationT, DatabaseFactory], ToolService],
        history_operation_provider: Callable[[], HistoryOperation],
        semantic_operation_provider: Callable[[], SemanticOperation],
        cursor_key: bytes | None = None,
    ) -> None:
        self._configuration_provider = configuration_provider
        self._index_service_factory = index_service_factory
        self._tool_service_factory = tool_service_factory
        self._history_operation_provider = history_operation_provider
        self._semantic_operation_provider = semantic_operation_provider
        self._cursor_key = cursor_key or secrets.token_bytes(32)

    def get_db(self, configuration: ConfigurationT | None = None) -> Database:
        active_settings = configuration or self._configuration_provider()
        active_settings.data_dir.mkdir(parents=True, exist_ok=True)
        return Database(str(active_settings.db_path))

    def _database_factory(self) -> Database:
        return self.get_db()

    def _index_service(self) -> IndexService:
        return self._index_service_factory(
            self._configuration_provider(), self._database_factory
        )

    def _tool_service(self) -> ToolService:
        return self._tool_service_factory(
            self._configuration_provider(), self._database_factory
        )

    def _query_service(self, database: Database) -> QueryService:
        codec = SymbolPageCursorCodec(self._cursor_key)
        return QueryService(SymbolRepository(database, codec))

    def list_symbols(
        self,
        project_name: str,
        limit: int = 50,
        cursor: str | None = None,
        kind: str | None = None,
        name: str | None = None,
        qualified_name: str | None = None,
        relative_path: str | None = None,
    ) -> str:
        """List symbols from the active immutable generation."""
        database = self.get_db()
        try:
            project_id = get_project_id(database, project_name)
            filters = SymbolFilters(kind, name, qualified_name, relative_path)
            result = self._query_service(database).list_symbols(
                project_id, filters, limit, cursor
            )
            return result.model_dump_json()
        except ValueError as error:
            code = (
                QueryErrorCode.PROJECT_NOT_FOUND
                if str(error) == "Project not found."
                else QueryErrorCode.LIMIT_EXCEEDED
            )
            message = (
                "Project not found."
                if code is QueryErrorCode.PROJECT_NOT_FOUND
                else "Query parameters are invalid."
            )
            return SymbolPageError(
                error=ApiError(code=code, message=message, retryable=False)
            ).model_dump_json()
        finally:
            database.close()

    def index_project(self, project_name: str, root_path: str) -> str:
        """Index supported files below a validated project root."""
        return self._index_service().index_project(project_name, root_path)

    def update_graph(self, project_name: str, filepaths: list[str]) -> str:
        """Update only validated files belonging to a registered project."""
        return self._index_service().update_graph(project_name, filepaths)

    def rebuild_database(self, confirmation: str) -> str:
        """Explicitly discard and recreate the current greenfield Karst database."""
        if confirmation != "DELETE_AND_REBUILD":
            return (
                "Rebuild rejected. This deletes the current Karst database without a "
                "backup; call rebuild_database(confirmation='DELETE_AND_REBUILD') to "
                "continue."
            )
        try:
            database = Database.rebuild_blocked_legacy_database(
                self._configuration_provider().db_path
            )
        except ValueError as error:
            return f"Rebuild rejected. {error}"
        database.close()
        return "Karst database deleted and rebuilt with the current schema."

    def query_symbol(self, project_name: str, symbol_name: str) -> str:
        """Return the definition location of a symbol."""
        return self._tool_service().query_symbol(project_name, symbol_name)

    def get_file_outline(self, project_name: str, filepath: str) -> str:
        """Return classes and functions defined in a file."""
        return self._tool_service().get_file_outline(project_name, filepath)

    def find_dependencies(self, project_name: str, symbol_name: str) -> str:
        """Return outgoing dependency edges for a symbol."""
        return self._tool_service().dependency_query(
            project_name, symbol_name, False, do_find_deps
        )

    def find_dependents(self, project_name: str, symbol_name: str) -> str:
        """Return incoming dependency edges for a symbol."""
        return self._tool_service().dependency_query(
            project_name, symbol_name, True, do_find_deps
        )

    def log_commit(
        self,
        project_name: str,
        commit_hash: str,
        message: str,
        files_changed: list[dict],
    ) -> str:
        """Store a commit and its changed files for a registered project."""
        return self._tool_service().log_commit(
            project_name, commit_hash, message, files_changed
        )

    def backfill_git_history(self, project_name: str, limit: int = 100) -> str:
        """Ingest bounded local Git history for a validated project."""
        return self._tool_service().backfill(
            project_name, limit, self._history_operation_provider()
        )

    def semantic_search(self, project_name: str, query: str, limit: int = 5) -> str:
        """Search pre-provisioned semantic embeddings for a project."""
        return self._tool_service().semantic_search(
            project_name, query, limit, self._semantic_operation_provider()
        )
