from __future__ import annotations

from collections.abc import Callable

from src.core_settings import core_settings
from src.karst_core.database.database_session import get_project_id
from src.karst_core.embeddings import do_semantic_search
from src.karst_core.git_history import do_backfill_git_history
from src.karst_core.indexing.service import ProjectIndexService
from src.karst_core.parser import CodeParser, ParseStatus, ParseSummary
from src.karst_mcp.contracts import define_tool_contracts
from src.karst_mcp.handlers import KarstToolHandlers
from src.karst_mcp.server import create_server
from src.tool_service import GraphToolService


_handlers = KarstToolHandlers(
    configuration_provider=lambda: core_settings,
    index_service_factory=lambda configuration, database_factory: ProjectIndexService(
        configuration, database_factory, CodeParser
    ),
    tool_service_factory=lambda configuration, database_factory: GraphToolService(
        configuration, database_factory
    ),
    history_operation_provider=lambda: do_backfill_git_history,
    semantic_operation_provider=lambda: do_semantic_search,
)

# Compatibility facade: legacy imports keep resolving to the composed handler object.
get_db = _handlers.get_db
list_symbols = _handlers.list_symbols
index_project = _handlers.index_project
update_graph = _handlers.update_graph
rebuild_database = _handlers.rebuild_database
query_symbol = _handlers.query_symbol
get_file_outline = _handlers.get_file_outline
find_dependencies = _handlers.find_dependencies
find_dependents = _handlers.find_dependents
log_commit = _handlers.log_commit
backfill_git_history = _handlers.backfill_git_history
semantic_search = _handlers.semantic_search

TOOL_CONTRACTS = define_tool_contracts(
    list_symbols,
    index_project,
    update_graph,
    rebuild_database,
    query_symbol,
    get_file_outline,
    find_dependencies,
    find_dependents,
    log_commit,
    backfill_git_history,
    semantic_search,
)
mcp = create_server("Karst", TOOL_CONTRACTS)

__all__ = [
    "CodeParser",
    "ParseStatus",
    "ParseSummary",
    "TOOL_CONTRACTS",
    "backfill_git_history",
    "find_dependencies",
    "find_dependents",
    "get_db",
    "get_file_outline",
    "get_project_id",
    "index_project",
    "list_symbols",
    "log_commit",
    "mcp",
    "query_symbol",
    "rebuild_database",
    "semantic_search",
    "update_graph",
]


def run(transport_runner: Callable[[], None] | None = None) -> None:
    """Run the MCP transport; injectable to keep entry-point behavior testable."""
    (transport_runner or mcp.run)()


if __name__ == "__main__":
    run()
