from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from src.database import Database
from src.database_session import database_session, get_project_id
from src.security import (
    PathSecurityPolicy,
    SecurityViolation,
    security_error,
    validate_registered_project,
    validate_project_name,
)
from src.settings import Settings


class GraphToolService:
    def __init__(
        self,
        configuration: Settings,
        database_factory: Callable[[], Database],
    ) -> None:
        self._settings = configuration
        self._database_factory = database_factory

    def query_symbol(self, project_name: str, symbol_name: str) -> str:
        started = time.monotonic()
        with database_session(self._database_factory) as database:
            project_id = self._project_or_error(database, project_name)
            if isinstance(project_id, str):
                return project_id
            node = database.get_node_by_name(project_id, symbol_name)
            if node is None:
                return f"Symbol '{symbol_name}' not found in project '{project_name}'."
            row = database.conn.execute(
                "SELECT path FROM files WHERE id = ?", (node["file_id"],)
            ).fetchone()
            path = row[0] if row else "Unknown file"
            response = (
                f"Symbol '{symbol_name}' ({node['type']}) defined in {path} "
                f"from line {node['start_line']} to {node['end_line']}."
            )
            self._telemetry(
                database,
                project_id,
                "query_symbol",
                started,
                self._tokens_saved(path, response),
            )
            return response

    def get_file_outline(self, project_name: str, file_path: str) -> str:
        started = time.monotonic()
        with database_session(self._database_factory) as database:
            project_id = self._project_or_error(database, project_name)
            if isinstance(project_id, str):
                return project_id
            row = database.conn.execute(
                "SELECT id FROM files WHERE project_id = ? AND path = ?",
                (project_id, file_path),
            ).fetchone()
            if row is None:
                return f"File '{file_path}' not found in project '{project_name}'."
            nodes = database.conn.execute(
                """
                SELECT name, type, start_line, end_line FROM nodes
                WHERE file_id = ? AND type IN ('class', 'function')
                ORDER BY start_line, end_line, name
                """,
                (row[0],),
            ).fetchall()
            if not nodes:
                return f"No classes or functions found in '{file_path}'."
            lines = [f"Outline for {file_path}:"]
            lines.extend(
                f"- {kind} {name} (lines {start}-{end})"
                for name, kind, start, end in nodes
            )
            response = "\n".join(lines)
            self._telemetry(
                database,
                project_id,
                "get_file_outline",
                started,
                self._tokens_saved(file_path, response),
            )
            return response

    def dependency_query(
        self,
        project_name: str,
        symbol_name: str,
        reverse: bool,
        operation: Callable[[Any, int, str, bool], tuple[str, float, int]],
    ) -> str:
        with database_session(self._database_factory) as database:
            project_id = self._project_or_error(database, project_name)
            if isinstance(project_id, str):
                return project_id
            response, latency, tokens = operation(
                database, project_id, symbol_name, reverse
            )
            tool_name = "find_dependents" if reverse else "find_dependencies"
            database.log_telemetry(project_id, tool_name, latency * 1000, tokens)
            return response

    def log_commit(
        self,
        project_name: str,
        commit_hash: str,
        message: str,
        files_changed: list[dict[str, Any]],
    ) -> str:
        started = time.monotonic()
        with database_session(self._database_factory) as database:
            project_id = self._project_or_error(database, project_name)
            if isinstance(project_id, str):
                return project_id
            database.log_commit(project_id, commit_hash, message, files_changed)
            self._telemetry(database, project_id, "log_commit", started, 0)
            return f"Logged commit {commit_hash} for project '{project_name}'."

    def backfill(
        self,
        project_name: str,
        limit: int,
        operation: Callable[[Any, int, str, str, int], str],
    ) -> str:
        with database_session(self._database_factory) as database:
            try:
                validate_project_name(project_name)
                row = database.conn.execute(
                    "SELECT id, path, owner, stable_id FROM projects WHERE name = ?",
                    (project_name,),
                ).fetchone()
                if row is None:
                    raise ValueError("Project not found.")
                project_path = validate_registered_project(
                    PathSecurityPolicy(self._settings.allowed_roots),
                    row[1],
                    row[2],
                    row[3],
                )
            except SecurityViolation as error:
                return security_error(error)
            except ValueError as error:
                return str(error)
            return operation(
                database,
                int(row[0]),
                project_name,
                str(project_path),
                limit,
            )

    def semantic_search(
        self,
        project_name: str,
        query: str,
        limit: int,
        operation: Callable[[Any, int, str, int], tuple[str, float, int]],
    ) -> str:
        with database_session(self._database_factory) as database:
            project_id = self._project_or_error(database, project_name)
            if isinstance(project_id, str):
                return project_id
            response, latency, tokens = operation(database, project_id, query, limit)
            database.log_telemetry(
                project_id, "semantic_search", latency * 1000, tokens
            )
            return response

    @staticmethod
    def _project_or_error(database: Database, project_name: str) -> int | str:
        try:
            return get_project_id(database, project_name)
        except ValueError as error:
            return str(error)

    @staticmethod
    def _tokens_saved(path: str, response: str) -> int:
        try:
            size = os.path.getsize(path) if os.path.exists(path) else 0
            return max(0, int((size - len(response)) / 4))
        except OSError:
            return 0

    @staticmethod
    def _telemetry(
        database: Database,
        project_id: int,
        name: str,
        started: float,
        tokens: int,
    ) -> None:
        database.log_telemetry(
            project_id,
            name,
            (time.monotonic() - started) * 1000,
            tokens,
        )
