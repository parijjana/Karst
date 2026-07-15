from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.karst_core.database.database import Database
from tests.database_v3_contract_support import (
    EXPECTED_COLUMNS,
    MANIFEST,
    PROJECT_ID,
    add_project,
    insert_staging,
)


def test_fresh_v3_has_exact_generation_graph_columns_and_defaults(
    tmp_path: Path,
) -> None:
    with Database(tmp_path / "fresh-v3.db") as database:
        for table, expected in EXPECTED_COLUMNS.items():
            actual = tuple(
                str(row[1])
                for row in database.conn.execute(f"PRAGMA table_info({table})")
            )
            assert actual == expected

        generation_defaults = {
            str(row[1]): row[4]
            for row in database.conn.execute("PRAGMA table_info(index_generations)")
        }
        diagnostic_defaults = {
            str(row[1]): row[4]
            for row in database.conn.execute("PRAGMA table_info(index_diagnostics)")
        }
        file_defaults = {
            str(row[1]): row[4]
            for row in database.conn.execute("PRAGMA table_info(files)")
        }

        assert generation_defaults["created_at"] == "CURRENT_TIMESTAMP"
        assert all(
            generation_defaults[field] == "0"
            for field in EXPECTED_COLUMNS["index_generations"][10:]
        )
        assert diagnostic_defaults["created_at"] == "CURRENT_TIMESTAMP"
        assert file_defaults["byte_size"] == "0"


def test_status_domain_and_exact_active_staging_partial_indexes(
    tmp_path: Path,
) -> None:
    with Database(tmp_path / "generation-status.db") as database:
        project_id = add_project(database, "first", PROJECT_ID)
        indexes = {
            str(row[0]): "".join(str(row[1]).lower().split())
            for row in database.conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'index' "
                "AND name LIKE 'ux_index_generations_%'"
            )
        }

        assert indexes["ux_index_generations_active_project"].endswith(
            "wherestatus='active'"
        )
        assert indexes["ux_index_generations_staging_project"].endswith(
            "wherestatus='staging'"
        )
        with pytest.raises(sqlite3.IntegrityError):
            database.conn.execute(
                "INSERT INTO index_generations "
                "(project_id, ordinal, status, completed_at, promoted_at, "
                "manifest_sha256, query_ready) VALUES "
                "(?, 2, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, 1)",
                (project_id, MANIFEST),
            )
        insert_staging(database, project_id)
        with pytest.raises(sqlite3.IntegrityError):
            insert_staging(database, project_id, 3)
        with pytest.raises(sqlite3.IntegrityError):
            database.conn.execute(
                "INSERT INTO index_generations (project_id, ordinal, status) "
                "VALUES (?, 4, 'complete')",
                (project_id,),
            )
        for ordinal, status in ((4, "failed"), (5, "cancelled")):
            database.conn.execute(
                "INSERT INTO index_generations "
                "(project_id, ordinal, status, completed_at, failure_code) "
                "VALUES (?, ?, ?, CURRENT_TIMESTAMP, 'legacy_terminal')",
                (project_id, ordinal, status),
            )
        database.conn.execute(
            "UPDATE index_generations SET status = 'superseded', "
            "superseded_at = CURRENT_TIMESTAMP WHERE project_id = ? AND status = 'active'",
            (project_id,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            database.conn.execute(
                "INSERT INTO index_generations "
                "(project_id, ordinal, status, completed_at, promoted_at, query_ready) "
                "VALUES (?, 6, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)",
                (project_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            database.conn.execute(
                "INSERT INTO index_generations "
                "(project_id, ordinal, status, completed_at, promoted_at, "
                "manifest_sha256, query_ready) VALUES "
                "(?, 6, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, 1)",
                (project_id, "g" * 64),
            )
        database.conn.execute(
            "INSERT INTO index_generations "
            "(project_id, ordinal, status, completed_at, promoted_at, "
            "manifest_sha256, query_ready) VALUES "
            "(?, 6, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, 1)",
            (project_id, MANIFEST),
        )
        assert {
            str(row[0])
            for row in database.conn.execute(
                "SELECT status FROM index_generations WHERE project_id = ?",
                (project_id,),
            )
        } == {"active", "superseded", "staging", "failed", "cancelled"}
