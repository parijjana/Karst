from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from src.karst_core.database.database import Database
from tests.database_v3_contract_support import (
    PROJECT_ID,
    SECOND_PROJECT_ID,
    active_generation,
    add_project,
    insert_staging,
)


def test_compatibility_repository_writes_and_reads_only_active_generation(
    tmp_path: Path,
) -> None:
    with Database(tmp_path / "compatibility.db") as database:
        project_id = add_project(database, "first", PROJECT_ID)
        active = active_generation(database, project_id)
        staging = insert_staging(database, project_id)

        file_id = database.add_file(project_id, "/first/src/a.py", "hash-one")
        node_id = database.add_node(project_id, file_id, "function", "run", 1, 2)
        edge_id = database.add_edge(project_id, node_id, node_id, "recursive")
        embedding_id = database.upsert_embedding(
            node_id,
            "[0.1]",
            content_hash="content",
            model_revision="model@1",
        )

        assert tuple(
            database.conn.execute(
                "SELECT generation_id, relative_path, byte_size FROM files WHERE id = ?",
                (file_id,),
            ).fetchone()
        ) == (active, "src/a.py", 0)
        assert tuple(
            database.conn.execute(
                "SELECT generation_id, language, qualified_name FROM nodes WHERE id = ?",
                (node_id,),
            ).fetchone()
        ) == (active, "python", "run")
        assert (
            database.conn.execute(
                "SELECT generation_id FROM edges WHERE id = ?", (edge_id,)
            ).fetchone()[0]
            == active
        )
        assert (
            database.conn.execute(
                "SELECT generation_id FROM embeddings WHERE id = ?", (embedding_id,)
            ).fetchone()[0]
            == active
        )
        node = database.get_node_by_name(project_id, "run")
        assert node is not None
        assert node["generation_id"] == active
        assert (
            database.conn.execute(
                "SELECT COUNT(*) FROM files WHERE generation_id = ?", (staging,)
            ).fetchone()[0]
            == 0
        )
        counts = database.conn.execute(
            "SELECT discovered_files, indexed_files, symbol_count, edge_count "
            "FROM index_generations WHERE id = ?",
            (active,),
        ).fetchone()
        assert tuple(counts) == (1, 1, 1, 1)
        assert not hasattr(database, "create_staging_generation")
        assert not hasattr(database, "promote_generation")


def test_cloned_generation_keeps_stable_ids_with_new_surrogate_rows(
    tmp_path: Path,
) -> None:
    with Database(tmp_path / "clone-identities.db") as database:
        project_id = add_project(database, "first", PROJECT_ID)
        file_id = database.add_file(project_id, "/first/a.py", "legacy-hash")
        node_id = database.add_node(project_id, file_id, "function", "run", 1, 2)
        staging = insert_staging(database, project_id)
        file_row = database.conn.execute(
            "SELECT stable_id, path, relative_path, identity_path, hash, byte_size FROM files "
            "WHERE id = ?",
            (file_id,),
        ).fetchone()
        node_row = database.conn.execute(
            "SELECT stable_id, language, type, name, qualified_name, signature, "
            "overload_discriminator, start_line, end_line FROM nodes WHERE id = ?",
            (node_id,),
        ).fetchone()
        cloned_file_id = file_id + 100
        cloned_node_id = node_id + 100
        database.conn.execute(
            "INSERT INTO files "
            "(id, project_id, generation_id, stable_id, path, relative_path, "
            "identity_path, hash, byte_size) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cloned_file_id, project_id, staging, *tuple(file_row)),
        )
        database.conn.execute(
            "INSERT INTO nodes "
            "(id, project_id, generation_id, file_id, stable_id, language, type, name, "
            "qualified_name, signature, overload_discriminator, start_line, end_line) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cloned_node_id, project_id, staging, cloned_file_id, *tuple(node_row)),
        )

        assert cloned_file_id != file_id
        assert cloned_node_id != node_id
        assert (
            database.conn.execute(
                "SELECT COUNT(DISTINCT stable_id) FROM files"
            ).fetchone()[0]
            == 1
        )
        assert (
            database.conn.execute(
                "SELECT COUNT(DISTINCT stable_id) FROM nodes"
            ).fetchone()[0]
            == 1
        )


def test_composite_foreign_keys_reject_cross_generation_and_project_rows(
    tmp_path: Path,
) -> None:
    with Database(tmp_path / "generation-fks.db") as database:
        first = add_project(database, "first", PROJECT_ID)
        second = add_project(database, "second", SECOND_PROJECT_ID)
        first_active = active_generation(database, first)
        second_active = active_generation(database, second)
        first_file = database.add_file(first, "/first/a.py", "hash")
        first_node = database.add_node(first, first_file, "function", "first", 1, 1)
        second_file = database.add_file(second, "/second/b.py", "hash")
        second_node = database.add_node(
            second, second_file, "function", "second", 1, 1
        )
        staging = insert_staging(database, first)

        with pytest.raises(sqlite3.IntegrityError):
            database.conn.execute(
                "INSERT INTO nodes "
                "(project_id, generation_id, file_id, stable_id, language, type, name, "
                "qualified_name, start_line, end_line) VALUES (?, ?, ?, ?, 'python', "
                "'function', 'bad', 'bad', 1, 1)",
                (first, staging, first_file, str(uuid5(NAMESPACE_URL, "bad-node"))),
            )
        with pytest.raises(sqlite3.IntegrityError):
            database.conn.execute(
                "INSERT INTO edges "
                "(project_id, generation_id, source_id, target_id, type) "
                "VALUES (?, ?, ?, ?, 'calls')",
                (first, first_active, first_node, second_node),
            )
        with pytest.raises(sqlite3.IntegrityError):
            database.conn.execute(
                "INSERT INTO embeddings "
                "(project_id, generation_id, node_id, vector) VALUES (?, ?, ?, '[0]')",
                (second, second_active, first_node),
            )
        with pytest.raises(sqlite3.IntegrityError):
            database.conn.execute(
                "INSERT INTO index_diagnostics "
                "(project_id, generation_id, severity, code, message) "
                "VALUES (?, ?, 'error', 'bad_scope', 'wrong project')",
                (second, first_active),
            )


def test_generation_diagnostics_persist_bounded_messages(tmp_path: Path) -> None:
    with Database(tmp_path / "diagnostics.db") as database:
        project_id = add_project(database, "first", PROJECT_ID)
        generation_id = active_generation(database, project_id)
        database.conn.execute(
            "INSERT INTO index_diagnostics "
            "(project_id, generation_id, relative_path, severity, code, message, "
            "exception_type) VALUES (?, ?, 'src/a.py', 'error', 'parse_failed', "
            "'Parse failed safely.', 'SyntaxError')",
            (project_id, generation_id),
        )
        with pytest.raises(sqlite3.IntegrityError):
            database.conn.execute(
                "INSERT INTO index_diagnostics "
                "(project_id, generation_id, severity, code, message) "
                "VALUES (?, ?, 'error', 'too_large', ?)",
                (project_id, generation_id, "x" * 4097),
            )

        assert tuple(
            database.conn.execute(
                "SELECT relative_path, severity, code, message, exception_type "
                "FROM index_diagnostics"
            ).fetchone()
        ) == (
            "src/a.py",
            "error",
            "parse_failed",
            "Parse failed safely.",
            "SyntaxError",
        )
