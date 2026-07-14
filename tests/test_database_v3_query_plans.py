from __future__ import annotations

from pathlib import Path

import pytest

from src.database import Database
from tests.database_v3_contract_support import MANIFEST


@pytest.mark.parametrize(
    ("sql", "parameters", "expected_index"),
    [
        (
            "SELECT * FROM index_generations WHERE project_id = ? AND status = 'active'",
            (1,),
            "ux_index_generations_active_project",
        ),
        (
            "SELECT * FROM index_generations WHERE project_id = ? AND manifest_sha256 = ?",
            (1, MANIFEST),
            "ix_index_generations_manifest",
        ),
        (
            "SELECT * FROM files WHERE project_id = ? AND generation_id = ? "
            "AND relative_path = ?",
            (1, 1, "a.py"),
            "ux_files_generation_relative_path",
        ),
        (
            "SELECT * FROM files INDEXED BY ix_files_generation_order "
            "WHERE project_id = ? AND generation_id = ? "
            "ORDER BY relative_path, id",
            (1, 1),
            "ix_files_generation_order",
        ),
        (
            "SELECT * FROM nodes WHERE project_id = ? AND generation_id = ? "
            "AND qualified_name = ?",
            (1, 1, "run"),
            "ix_nodes_generation_qualified_name",
        ),
        (
            "SELECT * FROM nodes WHERE project_id = ? AND generation_id = ? "
            "AND file_id = ? ORDER BY start_line, qualified_name, stable_id",
            (1, 1, 1),
            "ix_nodes_generation_file_order",
        ),
        (
            "SELECT * FROM edges INDEXED BY ix_edges_generation_source "
            "WHERE project_id = ? AND generation_id = ? "
            "AND source_id = ?",
            (1, 1, 1),
            "ix_edges_generation_source",
        ),
        (
            "SELECT * FROM edges INDEXED BY ix_edges_generation_target "
            "WHERE project_id = ? AND generation_id = ? "
            "AND target_id = ?",
            (1, 1, 1),
            "ix_edges_generation_target",
        ),
        (
            "SELECT * FROM embeddings INDEXED BY ux_embeddings_generation_node "
            "WHERE project_id = ? AND generation_id = ? "
            "AND node_id = ?",
            (1, 1, 1),
            "ux_embeddings_generation_node",
        ),
        (
            "SELECT * FROM index_diagnostics WHERE project_id = ? "
            "AND generation_id = ? ORDER BY id",
            (1, 1),
            "ix_index_diagnostics_generation",
        ),
    ],
)
def test_v3_query_plans_use_generation_indexes(
    tmp_path: Path,
    sql: str,
    parameters: tuple[object, ...],
    expected_index: str,
) -> None:
    with Database(tmp_path / "query-plans.db") as database:
        detail = " ".join(
            str(row[3])
            for row in database.conn.execute(f"EXPLAIN QUERY PLAN {sql}", parameters)
        )

    assert expected_index in detail


def test_keyset_join_uses_file_and_node_order_without_temp_sort(
    tmp_path: Path,
) -> None:
    with Database(tmp_path / "keyset-plan.db") as database:
        details = tuple(
            str(row[3])
            for row in database.conn.execute(
                "EXPLAIN QUERY PLAN SELECT node.stable_id FROM files AS file "
                "INDEXED BY ix_files_generation_order "
                "JOIN nodes AS node ON node.project_id = file.project_id "
                "AND node.generation_id = file.generation_id "
                "AND node.file_id = file.id WHERE file.project_id = ? "
                "AND file.generation_id = ? ORDER BY file.relative_path, file.id, "
                "node.start_line, node.qualified_name, node.stable_id",
                (1, 1),
            )
        )

    plan = " ".join(details)
    assert "ix_files_generation_order" in plan
    assert "ix_nodes_generation_file_order" in plan
    assert "USE TEMP B-TREE" not in plan
