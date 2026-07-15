"""Read-only, data-only project summaries for Mission Control consumers."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

@dataclass(frozen=True, slots=True)
class ProjectSummary:
    id: int
    name: str
    tracked_file_count: int
    nonblank_loc_total: int
    untracked_file_count: int
    untracked_folder_count: int
    discovered_not_indexed_count: int
    node_counts_by_type: dict[str, int]


@dataclass(frozen=True, slots=True)
class TrackedFileRow:
    id: int
    path: str
    hash: str
    nonblank_loc: int


class ProjectSummaryService:
    """Read models limited to active generations and project-relative paths."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def projects(self, limit: int, offset: int) -> list[dict[str, object]]:
        if not self._db_path.exists():
            return []
        with self._connection() as connection:
            if not _has_table(connection, "index_generations"):
                rows = connection.execute(
                    "SELECT id, name FROM projects ORDER BY id LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                return [
                    asdict(ProjectSummary(int(row[0]), str(row[1]), 0, 0, 0, 0, 0, {}))
                    for row in rows
                ]
            rows = connection.execute(
                "SELECT id, name FROM projects ORDER BY id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [asdict(self._project(connection, int(row[0]), str(row[1]))) for row in rows]

    def files(
        self, project_id: int, limit: int, offset: int
    ) -> list[dict[str, object]]:
        if not self._db_path.exists():
            return []
        with self._connection() as connection:
            if not _has_table(connection, "index_generations"):
                rows = connection.execute(
                    "SELECT id, path, hash FROM files WHERE project_id=? ORDER BY id LIMIT ? OFFSET ?",
                    (project_id, limit, offset),
                ).fetchall()
                return [
                    asdict(TrackedFileRow(int(row[0]), str(row[1]), str(row[2]), 0))
                    for row in rows
                ]
            rows = connection.execute(
                """SELECT f.id, f.relative_path, f.hash, f.nonblank_lines
                FROM files f JOIN index_generations g ON g.id=f.generation_id
                WHERE f.project_id=? AND g.status='active'
                ORDER BY f.relative_path, f.id LIMIT ? OFFSET ?""",
                (project_id, limit, offset),
            ).fetchall()
            return [
                asdict(
                    TrackedFileRow(int(row[0]), str(row[1]), str(row[2]), int(row[3]))
                )
                for row in rows
            ]

    @staticmethod
    def _project(
        connection: sqlite3.Connection, project_id: int, name: str
    ) -> ProjectSummary:
        generation = connection.execute(
            "SELECT id, discovered_files, indexed_files FROM index_generations "
            "WHERE project_id=? AND status='active'", (project_id,)
        ).fetchone()
        if generation is None:
            return ProjectSummary(project_id, name, 0, 0, 0, 0, 0, {})
        generation_id = int(generation[0])
        totals = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(nonblank_lines),0) FROM files "
            "WHERE project_id=? AND generation_id=?", (project_id, generation_id)
        ).fetchone()
        untracked = connection.execute(
            "SELECT kind, COUNT(*) FROM untracked_paths WHERE project_id=? AND generation_id=? GROUP BY kind",
            (project_id, generation_id),
        ).fetchall()
        kinds = {str(row[0]): int(row[1]) for row in untracked}
        node_rows = connection.execute(
            "SELECT type, COUNT(*) FROM nodes WHERE project_id=? AND generation_id=? GROUP BY type ORDER BY type",
            (project_id, generation_id),
        ).fetchall()
        return ProjectSummary(
            project_id,
            name,
            int(totals[0]),
            int(totals[1]),
            kinds.get("file", 0),
            kinds.get("folder", 0),
            max(0, int(generation[1]) - int(generation[2])),
            {str(row[0]): int(row[1]) for row in node_rows},
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        try:
            yield connection
        finally:
            connection.close()


def _has_table(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None
