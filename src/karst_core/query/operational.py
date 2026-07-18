"""Read-only operational data views independent from web transports."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator


EMPTY_STATS = {
    "total_projects": 0,
    "total_nodes": 0,
    "queries_served": 0,
    "tokens_saved": 0,
}


class OperationalReadService:
    """Serve dashboard read models without exposing database ownership to UI code."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def stats(self) -> dict[str, int]:
        if not self._db_path.exists():
            return dict(EMPTY_STATS)
        with self._connection() as connection:
            projects = self._count(connection, "projects")
            nodes = self._count(connection, "nodes")
            queries = 0
            tokens = 0
            if _has_tables(connection, "telemetry"):
                row = connection.execute(
                    "SELECT COUNT(*) AS c, SUM(tokens_saved) AS t FROM telemetry"
                ).fetchone()
                if row is not None:
                    queries = int(row["c"] or 0)
                    tokens = int(row["t"] or 0)
            return {
                "total_projects": projects,
                "total_nodes": nodes,
                "queries_served": queries,
                "tokens_saved": tokens,
            }

    def nodes(
        self, project_id: int, limit: int, offset: int
    ) -> list[dict[str, object]]:
        if not self._db_path.exists():
            return []
        with self._connection() as connection:
            if not _has_tables(connection, "nodes"):
                return []
            rows = connection.execute(
                """SELECT id, file_id, type, name, start_line, end_line FROM nodes
                WHERE project_id = ? ORDER BY id LIMIT ? OFFSET ?""",
                (project_id, limit, offset),
            ).fetchall()
            return _rows(rows)

    def project_telemetry(
        self, project_id: int, limit: int, offset: int
    ) -> list[dict[str, object]]:
        if not self._db_path.exists():
            return []
        with self._connection() as connection:
            if not _has_tables(connection, "telemetry"):
                return []
            rows = connection.execute(
                """SELECT id, tool_name, latency_ms, tokens_saved, timestamp
                FROM telemetry WHERE project_id = ?
                ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?""",
                (project_id, limit, offset),
            ).fetchall()
            return _rows(rows)

    def commits(
        self, project_id: int, limit: int, offset: int
    ) -> list[dict[str, object]]:
        if not self._db_path.exists():
            return []
        with self._connection() as connection:
            if not _has_tables(connection, "commits", "commit_files"):
                return []
            rows = connection.execute(
                """SELECT c.id, c.commit_hash, c.message, c.timestamp,
                       GROUP_CONCAT(cf.status || ':' || cf.file_path, ', ') AS files_changed
                FROM commits c LEFT JOIN commit_files cf ON c.id = cf.commit_id
                WHERE c.project_id = ? GROUP BY c.id
                ORDER BY c.timestamp DESC, c.id DESC LIMIT ? OFFSET ?""",
                (project_id, limit, offset),
            ).fetchall()
            return _rows(rows)

    def telemetry_aggregates(self, limit: int, offset: int) -> list[dict[str, object]]:
        if not self._db_path.exists():
            return []
        with self._connection() as connection:
            if not _has_tables(connection, "telemetry"):
                return []
            rows = connection.execute(
                """SELECT strftime('%Y-%m-%d %H:00', timestamp) AS time_bucket,
                       tool_name, COUNT(*) AS calls, AVG(latency_ms) AS avg_latency,
                       SUM(tokens_saved) AS total_tokens
                FROM telemetry GROUP BY time_bucket, tool_name
                ORDER BY time_bucket DESC, tool_name ASC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
            return _rows(rows)

    def service_metrics(self, limit: int, offset: int) -> list[dict[str, object]]:
        if not self._db_path.exists():
            return []
        with self._connection() as connection:
            if not _has_tables(connection, "telemetry"):
                return []
            rows = connection.execute(
                """SELECT id, tool_name AS service, latency_ms,
                       tokens_saved AS processed_count, details, timestamp
                FROM telemetry WHERE tool_name LIKE 'service:%'
                ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
            return _rows(rows)

    @staticmethod
    def _count(connection: sqlite3.Connection, table: str) -> int:
        if not _has_tables(connection, table):
            return 0
        row = connection.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
        return int(row["c"] if row is not None else 0)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()


def _has_tables(connection: sqlite3.Connection, *names: str) -> bool:
    placeholders = ",".join("?" for _ in names)
    row = connection.execute(
        f"SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ({placeholders})",
        names,
    ).fetchone()
    return row is not None and int(row[0]) == len(names)


def _rows(rows: list[sqlite3.Row]) -> list[dict[str, object]]:
    return [dict(row) for row in rows]
