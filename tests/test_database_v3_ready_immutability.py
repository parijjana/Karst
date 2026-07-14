from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from src.database import Database
from src.settings import TRUSTED_LOCAL_OWNER


PROJECT_ID = str(uuid5(NAMESPACE_URL, "project:/ready-immutability"))
MANIFEST = "a" * 64


def _add_project(database: Database) -> int:
    return database.add_project(
        "ready",
        "/ready",
        TRUSTED_LOCAL_OWNER,
        PROJECT_ID,
    )


def _active_generation(database: Database, project_id: int) -> int:
    return int(
        database.conn.execute(
            "SELECT id FROM index_generations "
            "WHERE project_id = ? AND status = 'active'",
            (project_id,),
        ).fetchone()[0]
    )


def _promote_ready(database: Database, generation_id: int) -> None:
    database.conn.execute(
        "UPDATE index_generations SET manifest_sha256 = ?, query_ready = 1 "
        "WHERE id = ?",
        (MANIFEST, generation_id),
    )


def _graph_state(database: Database) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = []
    for table in (
        "projects",
        "index_generations",
        "files",
        "nodes",
        "edges",
        "embeddings",
    ):
        rows.extend(
            (table, *tuple(row))
            for row in database.conn.execute(f"SELECT * FROM {table} ORDER BY id")
        )
    return tuple(rows)


def _seed_graph(database: Database) -> tuple[int, int, int, int, int]:
    project_id = _add_project(database)
    generation_id = _active_generation(database, project_id)
    file_id = database.add_file(project_id, "/ready/src/a.py", "hash-one")
    first_node = database.add_node(
        project_id, file_id, "function", "first", 1, 2
    )
    second_node = database.add_node(
        project_id, file_id, "function", "second", 3, 4
    )
    database.add_edge(project_id, first_node, second_node, "calls")
    database.upsert_embedding(
        first_node,
        "[0.1]",
        content_hash="content-one",
        model_revision="model@1",
    )
    _promote_ready(database, generation_id)
    return project_id, generation_id, file_id, first_node, second_node


@pytest.mark.parametrize(
    "operation",
    [
        "insert_file",
        "update_file",
        "insert_node",
        "existing_node",
        "insert_edge",
        "existing_edge",
        "update_embedding",
        "clear_project",
    ],
)
def test_compatibility_mutations_reject_query_ready_active_generation(
    tmp_path: Path,
    operation: str,
) -> None:
    with Database(tmp_path / f"ready-{operation}.db") as database:
        (
            project_id,
            generation_id,
            file_id,
            first_node,
            second_node,
        ) = _seed_graph(database)
        before = _graph_state(database)

        with pytest.raises(ValueError, match="writable active generation"):
            if operation == "insert_file":
                database.add_file(
                    project_id, "/ready/src/new.py", "hash-new"
                )
            elif operation == "update_file":
                database.add_file(
                    project_id, "/ready/src/a.py", "hash-changed"
                )
            elif operation == "insert_node":
                database.add_node(
                    project_id, file_id, "function", "third", 5, 6
                )
            elif operation == "existing_node":
                database.add_node(
                    project_id, file_id, "function", "first", 1, 2
                )
            elif operation == "insert_edge":
                database.add_edge(
                    project_id, second_node, first_node, "returns_to"
                )
            elif operation == "existing_edge":
                database.add_edge(
                    project_id, first_node, second_node, "calls"
                )
            elif operation == "update_embedding":
                database.upsert_embedding(
                    first_node,
                    "[9.9]",
                    content_hash="changed",
                    model_revision="model@2",
                )
            else:
                database.clear_project_data(project_id)

        assert _graph_state(database) == before
        generation = database.conn.execute(
            "SELECT query_ready, manifest_sha256, discovered_files, indexed_files, "
            "symbol_count, edge_count FROM index_generations WHERE id = ?",
            (generation_id,),
        ).fetchone()
        assert tuple(generation) == (1, MANIFEST, 1, 1, 2, 1)


def test_query_ready_active_generation_remains_readable(tmp_path: Path) -> None:
    with Database(tmp_path / "ready-reads.db") as database:
        project_id, generation_id, _, first_node, _ = _seed_graph(database)

        node = database.get_node_by_name(project_id, "first")
        edges = database.get_edges_for_node(first_node)

        assert node is not None
        assert node["generation_id"] == generation_id
        assert len(edges) == 1
        assert edges[0]["generation_id"] == generation_id


def test_writable_generation_check_and_mutation_hold_one_write_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "generation-write-lock.db"
    with Database(path) as writer, Database(path) as promoter:
        project_id = _add_project(writer)
        generation_id = _active_generation(writer, project_id)
        promoter.conn.execute("PRAGMA busy_timeout = 0")
        real_resolution = writer._writable_active_generation_id
        blocked_promotions: list[str] = []

        def resolve_during_promotion(target_project_id: int) -> int:
            resolved = real_resolution(target_project_id)
            with pytest.raises(sqlite3.OperationalError, match="locked") as error:
                promoter.conn.execute(
                    "UPDATE index_generations SET manifest_sha256 = ?, "
                    "query_ready = 1 WHERE id = ?",
                    (MANIFEST, resolved),
                )
            blocked_promotions.append(str(error.value))
            return resolved

        monkeypatch.setattr(
            writer,
            "_writable_active_generation_id",
            resolve_during_promotion,
        )

        file_id = writer.add_file(
            project_id,
            "/ready/src/locked.py",
            "locked-hash",
        )

        assert file_id > 0
        assert blocked_promotions and "locked" in blocked_promotions[0]
        assert not writer.conn.in_transaction
        assert not promoter.conn.in_transaction
        promoter.conn.execute(
            "UPDATE index_generations SET manifest_sha256 = ?, query_ready = 1 "
            "WHERE id = ?",
            (MANIFEST, generation_id),
        )
        with pytest.raises(ValueError, match="writable active generation"):
            writer.add_file(
                project_id,
                "/ready/src/after-promotion.py",
                "must-not-write",
            )
        assert (
            writer.conn.execute(
                "SELECT COUNT(*) FROM files WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()[0]
            == 1
        )
