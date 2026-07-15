from __future__ import annotations

from collections.abc import Callable
import secrets

from mcp.server.fastmcp import FastMCP

from src.karst_core.database.database import Database
from src.karst_core.database.database_session import get_project_id
from src.git_logic import do_backfill_git_history
from src.indexing_service import ProjectIndexService
from src.parser import CodeParser, ParseStatus, ParseSummary
from src.query_logic import do_find_deps, do_semantic_search
from src.query_cursor import SymbolFilters, SymbolPageCursorCodec
from src.query_service import QueryService
from src.query_models import ApiError, QueryErrorCode, SymbolPageError
from src.symbol_repository import SymbolRepository
from src.core_settings import CoreSettings, core_settings
from src.tool_service import GraphToolService


mcp = FastMCP("Karst")
_CURSOR_KEY = secrets.token_bytes(32)
__all__ = [
    "CodeParser",
    "ParseStatus",
    "ParseSummary",
    "backfill_git_history",
    "find_dependencies",
    "find_dependents",
    "get_db",
    "get_file_outline",
    "get_project_id",
    "index_project",
    "log_commit",
    "mcp",
    "query_symbol",
    "rebuild_database",
    "list_symbols",
    "semantic_search",
    "update_graph",
]


def get_db(configuration: CoreSettings | None = None) -> Database:
    active_settings = configuration or core_settings
    active_settings.data_dir.mkdir(parents=True, exist_ok=True)
    return Database(str(active_settings.db_path))


def _database_factory() -> Database:
    return get_db()


def _index_service() -> ProjectIndexService:
    return ProjectIndexService(core_settings, _database_factory, CodeParser)


def _tool_service() -> GraphToolService:
    return GraphToolService(core_settings, _database_factory)


def _query_service(
    database: Database, cursor_key: bytes | None = None
) -> QueryService:
    """Build the read-only query boundary with an injectable cursor key."""
    codec = SymbolPageCursorCodec(cursor_key if cursor_key is not None else _CURSOR_KEY)
    return QueryService(SymbolRepository(database, codec))


@mcp.tool()
def list_symbols(
    project_name: str,
    limit: int = 50,
    cursor: str | None = None,
    kind: str | None = None,
    name: str | None = None,
    qualified_name: str | None = None,
    relative_path: str | None = None,
) -> str:
    """List symbols from the active immutable generation."""
    database = get_db()
    try:
        project_id = get_project_id(database, project_name)
        filters = SymbolFilters(kind, name, qualified_name, relative_path)
        result = _query_service(database).list_symbols(project_id, filters, limit, cursor)
        return result.model_dump_json()
    except ValueError as error:
        code = (QueryErrorCode.PROJECT_NOT_FOUND
                if str(error) == "Project not found."
                else QueryErrorCode.LIMIT_EXCEEDED)
        message = "Project not found." if code is QueryErrorCode.PROJECT_NOT_FOUND else "Query parameters are invalid."
        return SymbolPageError(
            error=ApiError(code=code, message=message, retryable=False)
        ).model_dump_json()
    finally:
        database.close()


@mcp.tool()
def index_project(project_name: str, root_path: str) -> str:
    """Index supported files below a validated project root."""
    return _index_service().index_project(project_name, root_path)


@mcp.tool()
def update_graph(project_name: str, filepaths: list[str]) -> str:
    """Update only validated files belonging to a registered project."""
    return _index_service().update_graph(project_name, filepaths)


@mcp.tool()
def rebuild_database(confirmation: str) -> str:
    """Explicitly discard and recreate the current greenfield Karst database."""
    if confirmation != "DELETE_AND_REBUILD":
        return (
            "Rebuild rejected. This deletes the current Karst database without a "
            "backup; call rebuild_database(confirmation='DELETE_AND_REBUILD') to "
            "continue."
        )
    try:
        database = Database.rebuild_blocked_legacy_database(core_settings.db_path)
    except ValueError as error:
        return f"Rebuild rejected. {error}"
    database.close()
    return "Karst database deleted and rebuilt with the current schema."


@mcp.tool()
def query_symbol(project_name: str, symbol_name: str) -> str:
    """Return the definition location of a symbol."""
    return _tool_service().query_symbol(project_name, symbol_name)


@mcp.tool()
def get_file_outline(project_name: str, filepath: str) -> str:
    """Return classes and functions defined in a file."""
    return _tool_service().get_file_outline(project_name, filepath)


@mcp.tool()
def find_dependencies(project_name: str, symbol_name: str) -> str:
    """Return outgoing dependency edges for a symbol."""
    return _tool_service().dependency_query(
        project_name, symbol_name, False, do_find_deps
    )


@mcp.tool()
def find_dependents(project_name: str, symbol_name: str) -> str:
    """Return incoming dependency edges for a symbol."""
    return _tool_service().dependency_query(
        project_name, symbol_name, True, do_find_deps
    )


@mcp.tool()
def log_commit(
    project_name: str,
    commit_hash: str,
    message: str,
    files_changed: list[dict],
) -> str:
    """Store a commit and its changed files for a registered project."""
    return _tool_service().log_commit(project_name, commit_hash, message, files_changed)


@mcp.tool()
def backfill_git_history(project_name: str, limit: int = 100) -> str:
    """Ingest bounded local Git history for a validated project."""
    return _tool_service().backfill(project_name, limit, do_backfill_git_history)


@mcp.tool()
def semantic_search(project_name: str, query: str, limit: int = 5) -> str:
    """Search pre-provisioned semantic embeddings for a project."""
    return _tool_service().semantic_search(
        project_name, query, limit, do_semantic_search
    )


def run(transport_runner: Callable[[], None] | None = None) -> None:
    """Run the MCP transport; injectable to keep entry-point behavior testable."""
    (transport_runner or mcp.run)()


if __name__ == "__main__":
    run()
