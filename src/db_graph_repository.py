from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.db_generation_identity import (
    compatibility_absolute_path,
    derive_legacy_file_identity,
    derive_legacy_symbol_id,
    infer_language,
)
from src.db_transaction import TransactionRepositoryMixin
from src.settings import TRUSTED_LOCAL_OWNER


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    integrity_result: str
    foreign_key_violations: tuple[tuple[object, ...], ...]
    consistency_violations: tuple[tuple[str, int], ...]

    @property
    def ok(self) -> bool:
        return (
            self.integrity_result == "ok"
            and not self.foreign_key_violations
            and not self.consistency_violations
        )


class GenerationGraphRepositoryMixin(TransactionRepositoryMixin):
    """Legacy-compatible graph operations scoped to the active generation."""

    def add_project(self, name: str, path: str, owner: str, stable_id: str) -> int:
        if owner != TRUSTED_LOCAL_OWNER or not stable_id:
            raise ValueError("Projects must belong to the trusted local stdio domain.")
        with self.transaction():
            cursor = self.conn.execute(
                "INSERT INTO projects (name, path, owner, stable_id) VALUES (?, ?, ?, ?)",
                (name, path, owner, stable_id),
            )
            project_id = int(cursor.lastrowid or 0)
            self.conn.execute(
                "INSERT INTO index_generations "
                "(project_id, ordinal, status, completed_at, promoted_at, query_ready) "
                "VALUES (?, 1, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0)",
                (project_id,),
            )
        return project_id

    def add_file(self, project_id: int, path: str, file_hash: str) -> int:
        with self.transaction():
            project = self.conn.execute(
                "SELECT name, path, stable_id FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if project is None:
                raise ValueError("Project does not exist.")
            generation_id = self._writable_active_generation_id(project_id)
            absolute_path = compatibility_absolute_path(str(project[1]), path)
            identity = derive_legacy_file_identity(
                project_id,
                str(project[0]),
                str(project[1]),
                None if project[2] is None else str(project[2]),
                absolute_path,
            )
            existing = self.conn.execute(
                "SELECT id FROM files WHERE project_id = ? AND generation_id = ? "
                "AND stable_id = ?",
                (project_id, generation_id, identity.stable_id),
            ).fetchone()
            if existing is not None:
                self.conn.execute(
                    "UPDATE files SET path = ?, relative_path = ?, hash = ? WHERE id = ?",
                    (
                        absolute_path,
                        identity.relative_path,
                        file_hash,
                        int(existing[0]),
                    ),
                )
                return int(existing[0])
            cursor = self.conn.execute(
                "INSERT INTO files "
                "(project_id, generation_id, stable_id, path, relative_path, "
                "identity_path, hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    generation_id,
                    identity.stable_id,
                    absolute_path,
                    identity.relative_path,
                    identity.relative_path,
                    file_hash,
                ),
            )
            self.conn.execute(
                "UPDATE index_generations SET discovered_files = discovered_files + 1, "
                "indexed_files = indexed_files + 1 WHERE id = ?",
                (generation_id,),
            )
            return int(cursor.lastrowid or 0)

    def add_node(
        self,
        project_id: int,
        file_id: int,
        node_type: str,
        name: str,
        start_line: int,
        end_line: int,
    ) -> int:
        with self.transaction():
            generation_id = self._writable_active_generation_id(project_id)
            file_row = self.conn.execute(
                "SELECT stable_id, relative_path FROM files WHERE id = ? "
                "AND project_id = ? AND generation_id = ?",
                (file_id, project_id, generation_id),
            ).fetchone()
            if file_row is None:
                raise ValueError("File does not belong to project active generation.")
            existing = self.conn.execute(
                "SELECT id FROM nodes WHERE project_id = ? AND generation_id = ? "
                "AND file_id = ? AND type = ? AND name = ? AND start_line = ? "
                "AND end_line = ?",
                (
                    project_id,
                    generation_id,
                    file_id,
                    node_type,
                    name,
                    start_line,
                    end_line,
                ),
            ).fetchone()
            if existing is not None:
                return int(existing[0])
            node_id = int(
                self.conn.execute(
                    "SELECT COALESCE(MAX(id), 0) + 1 FROM nodes"
                ).fetchone()[0]
            )
            language = infer_language(str(file_row[1]))
            overload = f"legacy:{node_id}"
            stable_id = derive_legacy_symbol_id(
                str(file_row[0]), language, node_type, name, overload
            )
            self.conn.execute(
                "INSERT INTO nodes "
                "(id, project_id, generation_id, file_id, stable_id, language, type, "
                "name, qualified_name, overload_discriminator, start_line, end_line) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    node_id,
                    project_id,
                    generation_id,
                    file_id,
                    stable_id,
                    language,
                    node_type,
                    name,
                    name,
                    overload,
                    start_line,
                    end_line,
                ),
            )
            self.conn.execute(
                "UPDATE index_generations SET symbol_count = symbol_count + 1 "
                "WHERE id = ?",
                (generation_id,),
            )
            return node_id

    def add_edge(
        self, project_id: int, source_id: int, target_id: int, edge_type: str
    ) -> int:
        with self.transaction():
            generation_id = self._writable_active_generation_id(project_id)
            endpoints = self.conn.execute(
                "SELECT id FROM nodes WHERE project_id = ? AND generation_id = ? "
                "AND id IN (?, ?)",
                (project_id, generation_id, source_id, target_id),
            ).fetchall()
            if len(endpoints) != len({source_id, target_id}):
                raise ValueError(
                    "Edge endpoints do not belong to project active generation."
                )
            identity = (project_id, generation_id, source_id, target_id, edge_type)
            existing = self.conn.execute(
                "SELECT id FROM edges WHERE project_id = ? AND generation_id = ? "
                "AND source_id = ? AND target_id = ? AND type = ?",
                identity,
            ).fetchone()
            if existing is not None:
                return int(existing[0])
            cursor = self.conn.execute(
                "INSERT INTO edges "
                "(project_id, generation_id, source_id, target_id, type) "
                "VALUES (?, ?, ?, ?, ?)",
                identity,
            )
            self.conn.execute(
                "UPDATE index_generations SET edge_count = edge_count + 1 WHERE id = ?",
                (generation_id,),
            )
            return int(cursor.lastrowid or 0)

    def get_node_by_name(self, project_id: int, name: str) -> dict[str, Any] | None:
        self._ensure_open()
        row = self.conn.execute(
            "SELECT node.* FROM nodes AS node JOIN index_generations AS generation "
            "ON generation.id = node.generation_id AND generation.project_id = "
            "node.project_id WHERE node.project_id = ? AND node.name = ? "
            "AND generation.status = 'active' ORDER BY node.id LIMIT 1",
            (project_id, name),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_edges_for_node(self, node_id: int) -> list[dict[str, Any]]:
        self._ensure_open()
        rows = self.conn.execute(
            "SELECT edge.* FROM edges AS edge JOIN index_generations AS generation "
            "ON generation.id = edge.generation_id AND generation.project_id = "
            "edge.project_id WHERE (edge.source_id = ? OR edge.target_id = ?) "
            "AND generation.status = 'active' ORDER BY edge.id",
            (node_id, node_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def clear_project_data(self, project_id: int) -> None:
        with self.transaction():
            self._writable_active_generation_id(project_id)
            self.conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    def _writable_active_generation_id(self, project_id: int) -> int:
        row = self.conn.execute(
            "SELECT id FROM index_generations WHERE project_id = ? "
            "AND status = 'active' AND query_ready = 0",
            (project_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Project has no writable active generation.")
        return int(row[0])


class OperationalRepositoryMixin(GenerationGraphRepositoryMixin):
    def log_telemetry(
        self,
        project_id: int | None,
        tool_name: str,
        latency_ms: float,
        tokens_saved: int = 0,
        details: str | None = None,
    ) -> int:
        self._before_write()
        cursor = self.conn.execute(
            "INSERT INTO telemetry "
            "(project_id, tool_name, latency_ms, tokens_saved, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, tool_name, latency_ms, tokens_saved, details),
        )
        return int(cursor.lastrowid or 0)

    def log_commit(
        self,
        project_id: int,
        commit_hash: str,
        message: str | None,
        files_changed: Sequence[Mapping[str, object]],
    ) -> int:
        with self.transaction():
            self.conn.execute(
                "INSERT INTO commits (project_id, commit_hash, message) VALUES (?, ?, ?) "
                "ON CONFLICT(project_id, commit_hash) DO UPDATE SET message = excluded.message",
                (project_id, commit_hash, message or ""),
            )
            commit_id = int(
                self.conn.execute(
                    "SELECT id FROM commits WHERE project_id = ? AND commit_hash = ?",
                    (project_id, commit_hash),
                ).fetchone()[0]
            )
            self.conn.execute(
                "DELETE FROM commit_files WHERE commit_id = ?", (commit_id,)
            )
            for changed in files_changed:
                self.conn.execute(
                    "INSERT INTO commit_files (commit_id, file_path, status) VALUES (?, ?, ?)",
                    (
                        commit_id,
                        str(changed.get("path", "")),
                        str(changed.get("status", "modified")),
                    ),
                )
        return commit_id
