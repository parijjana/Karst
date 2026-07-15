from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from src.database import Database
from src.database_session import database_session, get_project_id
from src.index_generation_service import IncrementalIndexService
from src.index_models import IndexResult
from src.parser import CodeParser, ParseOutcome, ParseStatus, ParseSummary
from src.security import (
    PathSecurityPolicy,
    SecurityViolation,
    security_error,
    stable_project_id,
    validate_registered_project,
    validate_project_name,
)
from src.core_settings import CoreSettings, TRUSTED_LOCAL_OWNER


SUPPORTED_EXTENSIONS = frozenset({".py", ".js", ".ts", ".dart", ".md"})
IGNORED_DIRECTORIES = frozenset(
    {"node_modules", "build", "dist", "__pycache__", "out", "target"}
)


class ProjectIndexService:
    def __init__(
        self,
        configuration: CoreSettings,
        database_factory: Callable[[], Database],
        parser_factory: Callable[[], CodeParser] = CodeParser,
    ) -> None:
        self._settings = configuration
        self._database_factory = database_factory
        self._parser_factory = parser_factory
        self._policy = PathSecurityPolicy(configuration.allowed_roots)

    def index_project(self, project_name: str, root_path: str) -> str:
        started = time.monotonic()
        try:
            validate_project_name(project_name)
            root = self._policy.validate_project_root(root_path)
            # Retain the established traversal safety check before any project
            # registration or generation work can mutate stored graph data.
            self._policy.discover_project_files(
                root, set(SUPPORTED_EXTENSIONS), set(IGNORED_DIRECTORIES)
            )
        except SecurityViolation as error:
            return security_error(error)

        with database_session(self._database_factory) as database:
            try:
                project_id = self._register_project(database, project_name, root)
            except SecurityViolation as error:
                return security_error(error)
            except sqlite3.IntegrityError:
                return "Unable to register project."

        result = self._generation_index(project_id, root)
        with database_session(self._database_factory) as database:
            database.log_telemetry(
                project_id,
                "index_project",
                (time.monotonic() - started) * 1000,
                0,
            )
        return self._generation_summary("Indexed", project_name, result)

    def update_graph(self, project_name: str, filepaths: list[str]) -> str:
        started = time.monotonic()
        with database_session(self._database_factory) as database:
            try:
                project_id = get_project_id(database, project_name)
            except ValueError as error:
                return str(error)

            row = database.conn.execute(
                "SELECT path, owner, stable_id FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if row is None:
                return "Project not found."
            try:
                root = validate_registered_project(self._policy, row[0], row[1], row[2])
                for path in dict.fromkeys(filepaths):
                    self._policy.validate_project_file(path, root)
            except SecurityViolation as error:
                return security_error(error)

        result = self._generation_index(project_id, root)
        with database_session(self._database_factory) as database:
            database.log_telemetry(
                project_id,
                "update_graph",
                (time.monotonic() - started) * 1000,
                0,
            )
        return self._generation_summary("Updated", project_name, result)

    def _register_project(
        self, database: Database, project_name: str, root: Path
    ) -> int:
        row = database.conn.execute(
            "SELECT id, path, owner, stable_id FROM projects WHERE name = ?",
            (project_name,),
        ).fetchone()
        identity = stable_project_id(root)
        if row is not None:
            stored_root = validate_registered_project(
                self._policy, row[1], row[2], row[3]
            )
            if stored_root != root:
                raise SecurityViolation("project_identity_conflict")
            return int(row[0])
        return database.add_project(
            project_name,
            str(root),
            owner=TRUSTED_LOCAL_OWNER,
            stable_id=identity,
        )

    def _parse_files(
        self,
        database: Database,
        project_id: int,
        files: Iterable[Path],
    ) -> tuple[ParseSummary, int]:
        parser = self._parser_factory()
        outcomes: list[ParseOutcome] = []
        tokens_saved = 0
        for path in files:
            outcome = parser.parse_file(database, project_id, path)
            outcomes.append(outcome)
            if outcome.status is ParseStatus.INDEXED:
                try:
                    tokens_saved += path.stat().st_size // 4
                except OSError:
                    pass
        return ParseSummary(tuple(outcomes)), tokens_saved

    def _generation_index(self, project_id: int, root: Path) -> IndexResult:
        service = IncrementalIndexService(
            self._database_factory,
            self._policy,
            self._parser_factory,
        )
        return service.index(
            project_id,
            root,
            extensions=tuple(SUPPORTED_EXTENSIONS),
            ignored_directories=tuple(IGNORED_DIRECTORIES),
        )

    @staticmethod
    def _generation_summary(
        action: str, project_name: str, result: IndexResult
    ) -> str:
        counts = result.counts
        return (
            f"{action} {counts.indexed_files} files for project "
            f"'{project_name}'; skipped {counts.skipped_files}; "
            f"failed {counts.failed_files}."
        )

    @staticmethod
    def _summary(action: str, project_name: str, summary: ParseSummary) -> str:
        return (
            f"{action} {summary.indexed_count} files for project "
            f"'{project_name}'; skipped {summary.skipped_count}; "
            f"failed {summary.failed_count}."
        )
