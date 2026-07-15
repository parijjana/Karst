"""Read-only structural graph for Mission Control visualizations.

The graph deliberately represents containment, not Karst's symbol dependency graph.
It is sourced only from query-ready active generations, so consumers never render an
incomplete index as an authoritative project map.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
import sqlite3
from typing import Iterator


@dataclass(frozen=True, slots=True)
class StructuralGraph:
    nodes: list[dict[str, object]]
    links: list[dict[str, object]]

    def as_dict(self) -> dict[str, list[dict[str, object]]]:
        return asdict(self)


class StructuralGraphService:
    """Build a hierarchy suitable for progressive project/folder exploration."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def graph(self, project_id: int | None = None) -> StructuralGraph:
        if not self._db_path.exists():
            return StructuralGraph([], [])
        with self._connection() as connection:
            projects = self._projects(connection, project_id)
            if not projects:
                return StructuralGraph([], [])
            nodes: list[dict[str, object]] = [
                {"id": "karst", "type": "karst", "weight": len(projects)}
            ]
            links: list[dict[str, object]] = []
            for project in projects:
                self._append_project(connection, project, nodes, links)
            return StructuralGraph(nodes, links)

    @staticmethod
    def _projects(
        connection: sqlite3.Connection, project_id: int | None
    ) -> list[sqlite3.Row]:
        where = ""
        arguments: tuple[object, ...] = ()
        if project_id is not None:
            where = "AND project.id = ?"
            arguments = (project_id,)
        return connection.execute(
            """SELECT project.id, project.name, project.stable_id, generation.id AS generation_id
            FROM projects AS project
            JOIN index_generations AS generation ON generation.project_id = project.id
            WHERE generation.status='active' AND generation.query_ready=1 """
            + where
            + " ORDER BY project.id",
            arguments,
        ).fetchall()

    def _append_project(
        self,
        connection: sqlite3.Connection,
        project: sqlite3.Row,
        nodes: list[dict[str, object]],
        links: list[dict[str, object]],
    ) -> None:
        project_key = _opaque_id("project", str(project["stable_id"]))
        generation_id = int(project["generation_id"])
        files = connection.execute(
            """SELECT id, stable_id, relative_path FROM files
            WHERE project_id=? AND generation_id=? ORDER BY relative_path, id""",
            (int(project["id"]), generation_id),
        ).fetchall()
        counts = {
            int(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT file_id, COUNT(*) FROM nodes WHERE project_id=? AND generation_id=? GROUP BY file_id",
                (int(project["id"]), generation_id),
            ).fetchall()
        }
        project_weight = sum(counts.values())
        nodes.append(
            {
                "id": project_key,
                "type": "project",
                "weight": project_weight,
                "ancestor_ids": ["karst"],
                "detail": {"project_id": int(project["id"]), "name": str(project["name"])},
            }
        )
        links.append({"source": "karst", "target": project_key, "type": "structural"})

        folders: set[str] = set()
        for file in files:
            folders.update(_ancestors(str(file["relative_path"])))
        folder_weights = {
            folder: sum(
                counts.get(int(file["id"]), 0)
                for file in files
                if _is_descendant(str(file["relative_path"]), folder)
            )
            for folder in folders
        }
        folder_ids = {folder: _opaque_id("folder", str(project["stable_id"]), folder) for folder in folders}
        for folder in sorted(folders, key=lambda value: (value.count("/"), value)):
            parent = str(PurePosixPath(folder).parent)
            parent_id = project_key if parent == "." else folder_ids[parent]
            nodes.append(
                {
                    "id": folder_ids[folder],
                    "type": "folder",
                    "weight": folder_weights[folder],
                    "parent_id": parent_id,
                    "ancestor_ids": _ancestor_ids(folder, folder_ids, project_key),
                    "detail": {"path": folder, "name": PurePosixPath(folder).name},
                }
            )
            links.append({"source": parent_id, "target": folder_ids[folder], "type": "structural"})
        file_ids: dict[int, str] = {}
        for file in files:
            relative_path = str(file["relative_path"])
            file_key = _opaque_id("file", str(file["stable_id"]))
            file_ids[int(file["id"])] = file_key
            parent = str(PurePosixPath(relative_path).parent)
            parent_id = project_key if parent == "." else folder_ids[parent]
            nodes.append(
                {
                    "id": file_key,
                    "type": "file",
                    "weight": counts.get(int(file["id"]), 0),
                    "parent_id": parent_id,
                    "ancestor_ids": _ancestor_ids(parent, folder_ids, project_key),
                    "detail": {"path": relative_path, "name": PurePosixPath(relative_path).name},
                }
            )
            links.append({"source": parent_id, "target": file_key, "type": "structural"})
        code_rows = connection.execute(
            """SELECT id, stable_id, file_id, type FROM nodes
            WHERE project_id=? AND generation_id=? ORDER BY id""",
            (int(project["id"]), generation_id),
        ).fetchall()
        for code in code_rows:
            code_key = _opaque_id("code", str(code["stable_id"]))
            nodes.append(
                {
                    "id": code_key,
                    "type": "code_dot",
                    "weight": 1,
                    "parent_id": file_ids[int(code["file_id"])],
                }
            )
            links.append(
                {
                    "source": file_ids[int(code["file_id"])],
                    "target": code_key,
                    "type": "code_node",
                    "node_type": str(code["type"]),
                }
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()


def _opaque_id(kind: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{kind}_{digest}"


def _ancestors(relative_path: str) -> set[str]:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or ".." in path.parts:
        return set()
    ancestors: set[str] = set()
    parent = path.parent
    while str(parent) != ".":
        ancestors.add(parent.as_posix())
        parent = parent.parent
    return ancestors


def _is_descendant(relative_path: str, folder: str) -> bool:
    return relative_path == folder or relative_path.startswith(f"{folder}/")


def _ancestor_ids(
    folder: str, folder_ids: dict[str, str], project_id: str
) -> list[str]:
    """Return opaque context from Karst through the containing hierarchy."""
    if folder == ".":
        return ["karst", project_id]
    parts = PurePosixPath(folder).parts
    return ["karst", project_id, *[folder_ids["/".join(parts[:index])] for index in range(1, len(parts) + 1)]]
