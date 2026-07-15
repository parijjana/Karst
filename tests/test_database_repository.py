from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.karst_core.database.database import Database
from src.settings import TRUSTED_LOCAL_OWNER


def _project(database: Database, name: str = "project") -> int:
    return database.add_project(
        name,
        f"/projects/{name}",
        TRUSTED_LOCAL_OWNER,
        f"stable:{name}",
    )


def _node(database: Database, project_id: int) -> int:
    file_id = database.add_file(project_id, "/projects/project/a.py", "hash")
    return database.add_node(project_id, file_id, "function", "run", 1, 2)


def test_auto_commit_wrappers_remain_visible_to_other_connections(
    tmp_path: Path,
) -> None:
    path = tmp_path / "auto-commit.db"
    with Database(path) as database:
        _project(database)
        observer = sqlite3.connect(path)
        assert observer.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        observer.close()


def test_unit_of_work_batches_writes_until_outer_commit(tmp_path: Path) -> None:
    path = tmp_path / "batch.db"
    with Database(path) as database:
        observer = sqlite3.connect(path)
        with database.transaction():
            project_id = _project(database)
            _node(database, project_id)
            assert database.conn.in_transaction
            assert observer.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0

        assert observer.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert observer.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 1
        observer.close()


def test_unit_of_work_rolls_back_all_writes_on_failure(tmp_path: Path) -> None:
    path = tmp_path / "rollback.db"
    with Database(path) as database:
        with pytest.raises(RuntimeError, match="injected"):
            with database.transaction():
                project_id = _project(database)
                _node(database, project_id)
                raise RuntimeError("injected")

        assert database.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
        assert database.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0


def test_nested_unit_of_work_uses_savepoint(tmp_path: Path) -> None:
    with Database(tmp_path / "nested.db") as database:
        with database.transaction():
            project_id = _project(database)
            with pytest.raises(ValueError, match="inner"):
                with database.transaction():
                    database.add_file(project_id, "/projects/project/b.py", "hash")
                    raise ValueError("inner")
            database.add_file(project_id, "/projects/project/a.py", "hash")

        paths = database.conn.execute("SELECT path FROM files ORDER BY path").fetchall()
        assert [row[0] for row in paths] == ["/projects/project/a.py"]


def test_context_manager_closes_connection_deterministically(tmp_path: Path) -> None:
    database = Database(tmp_path / "closed.db")
    with database as entered:
        assert entered is database
        assert not database.closed

    assert database.closed
    database.close()
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        database.conn.execute("SELECT 1")


def test_file_node_and_edge_inserts_are_idempotent(tmp_path: Path) -> None:
    with Database(tmp_path / "identities.db") as database:
        project_id = _project(database)
        file_id = database.add_file(project_id, "/projects/project/a.py", "old")
        same_file_id = database.add_file(
            project_id, "/projects/project/a.py", "new"
        )
        source_id = database.add_node(
            project_id, file_id, "function", "source", 1, 2
        )
        same_source_id = database.add_node(
            project_id, file_id, "function", "source", 1, 2
        )
        target_id = database.add_node(
            project_id, file_id, "function", "target", 4, 5
        )
        edge_id = database.add_edge(project_id, source_id, target_id, "calls")
        same_edge_id = database.add_edge(
            project_id, source_id, target_id, "calls"
        )

        assert same_file_id == file_id
        assert same_source_id == source_id
        assert same_edge_id == edge_id
        assert database.conn.execute(
            "SELECT hash FROM files WHERE id = ?", (file_id,)
        ).fetchone()[0] == "new"
        assert database.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 2
        assert database.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 1


def test_commit_and_embedding_ingestion_are_idempotent(tmp_path: Path) -> None:
    with Database(tmp_path / "ingestion.db") as database:
        project_id = _project(database)
        node_id = _node(database, project_id)
        commit_id = database.log_commit(
            project_id,
            "abc123",
            "first message",
            [{"path": "a.py", "status": "M"}],
        )
        same_commit_id = database.log_commit(
            project_id,
            "abc123",
            "corrected message",
            [
                {"path": "a.py", "status": "A"},
                {"path": "b.py", "status": "M"},
            ],
        )
        embedding_id = database.upsert_embedding(
            node_id, "[0.1]", content_hash="one", model_revision="model@1"
        )
        same_embedding_id = database.upsert_embedding(
            node_id, "[0.2]", content_hash="two", model_revision="model@1"
        )

        assert same_commit_id == commit_id
        assert same_embedding_id == embedding_id
        assert database.conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 1
        assert database.conn.execute("SELECT message FROM commits").fetchone()[0] == (
            "corrected message"
        )
        assert database.conn.execute("SELECT COUNT(*) FROM commit_files").fetchone()[0] == 2
        assert database.conn.execute(
            "SELECT status FROM commit_files WHERE file_path = 'a.py'"
        ).fetchone()[0] == "A"
        assert tuple(
            database.conn.execute(
                "SELECT vector, content_hash, model_revision FROM embeddings"
            ).fetchone()
        ) == ("[0.2]", "two", "model@1")


@pytest.mark.parametrize(
    ("sql", "parameters", "expected_index"),
    [
        (
            "SELECT * FROM nodes WHERE project_id = ? AND name = ?",
            (1, "run"),
            "ix_nodes_project_name",
        ),
        (
            "SELECT * FROM edges WHERE source_id = ?",
            (1,),
            "ix_edges_source",
        ),
        (
            "SELECT * FROM commits WHERE project_id = ? ORDER BY timestamp DESC",
            (1,),
            "ix_commits_project_timestamp",
        ),
        (
            "SELECT * FROM telemetry WHERE timestamp < datetime('now', '-30 days')",
            (),
            "ix_telemetry_timestamp",
        ),
        (
            "SELECT * FROM embeddings WHERE node_id = ?",
            (1,),
            "ux_embeddings_node",
        ),
    ],
)
def test_query_plans_use_lookup_indexes(
    tmp_path: Path,
    sql: str,
    parameters: tuple[object, ...],
    expected_index: str,
) -> None:
    with Database(tmp_path / "plans.db") as database:
        details = database.conn.execute(
            f"EXPLAIN QUERY PLAN {sql}", parameters
        ).fetchall()

    assert expected_index in " ".join(str(row[3]) for row in details)


def test_constraints_reject_invalid_direct_writes(tmp_path: Path) -> None:
    with Database(tmp_path / "constraints.db") as database:
        project_id = _project(database)
        file_id = database.add_file(project_id, "/projects/project/a.py", "hash")

        with pytest.raises(sqlite3.IntegrityError):
            database.conn.execute(
                "INSERT INTO files (project_id, path, hash) VALUES (?, ?, ?)",
                (project_id, "/projects/project/a.py", "duplicate"),
            )
        database.conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            database.conn.execute(
                "INSERT INTO nodes "
                "(project_id, file_id, type, name, start_line, end_line) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, file_id, "function", "bad", 0, -1),
            )
        database.conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            database.conn.execute(
                "INSERT INTO embeddings (node_id, vector) VALUES (?, ?)",
                (999999, "[0.0]"),
            )

        database.conn.rollback()
        assert database.integrity_report().ok
