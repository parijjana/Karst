from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.karst_core.database.database import Database
from src.karst_core.database.db_migrations import MigrationError


def test_wrong_active_partial_index_predicate_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "wrong-active-index.db"
    with Database(path):
        pass
    connection = sqlite3.connect(path)
    connection.execute("DROP INDEX ux_index_generations_active_project")
    connection.execute(
        "CREATE UNIQUE INDEX ux_index_generations_active_project "
        "ON index_generations(project_id) WHERE status != 'active'"
    )
    connection.commit()
    connection.close()

    with pytest.raises(MigrationError, match="partial-index predicate"):
        Database(path)


def test_quoted_v3_timestamp_default_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "quoted-v3-default.db"
    with Database(path):
        pass
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("ALTER TABLE index_diagnostics RENAME TO old_diagnostics")
    connection.execute(
        "CREATE TABLE index_diagnostics ("
        "id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, "
        "generation_id INTEGER NOT NULL, relative_path TEXT, "
        "severity TEXT NOT NULL CHECK(severity IN ('info','warning','error','fatal')), "
        "code TEXT NOT NULL CHECK(length(code) BETWEEN 1 AND 64), "
        "message TEXT NOT NULL CHECK(length(message) BETWEEN 1 AND 4096), "
        "exception_type TEXT CHECK(exception_type IS NULL OR "
        "length(exception_type) BETWEEN 1 AND 256), "
        "created_at DATETIME NOT NULL DEFAULT 'CURRENT_TIMESTAMP', "
        "FOREIGN KEY(project_id, generation_id) REFERENCES "
        "index_generations(project_id, id) ON DELETE CASCADE)"
    )
    connection.execute("DROP TABLE old_diagnostics")
    connection.execute(
        "CREATE INDEX ix_index_diagnostics_generation "
        "ON index_diagnostics(project_id, generation_id, id)"
    )
    connection.commit()
    connection.close()

    with pytest.raises(MigrationError, match="default|table definition"):
        Database(path)


def test_v3_table_check_tamper_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "v3-table-check.db"
    with Database(path):
        pass
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("ALTER TABLE index_diagnostics RENAME TO old_diagnostics")
    connection.execute(
        "CREATE TABLE index_diagnostics ("
        "id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, "
        "generation_id INTEGER NOT NULL, relative_path TEXT, "
        "severity TEXT NOT NULL CHECK(severity IN ('info','warning','error','fatal')), "
        "code TEXT NOT NULL CHECK(length(code) BETWEEN 1 AND 64), "
        "message TEXT NOT NULL CHECK(length(message) BETWEEN 1 AND 8192), "
        "exception_type TEXT CHECK(exception_type IS NULL OR "
        "length(exception_type) BETWEEN 1 AND 256), "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY(project_id, generation_id) REFERENCES "
        "index_generations(project_id, id) ON DELETE CASCADE)"
    )
    connection.execute("DROP TABLE old_diagnostics")
    connection.execute(
        "CREATE INDEX ix_index_diagnostics_generation "
        "ON index_diagnostics(project_id, generation_id, id)"
    )
    connection.commit()
    connection.close()

    with pytest.raises(MigrationError, match="table definition"):
        Database(path)


def test_v3_migration_checksum_tamper_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "v3-checksum.db"
    with Database(path):
        pass
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 3"
    )
    connection.commit()
    connection.close()

    with pytest.raises(MigrationError, match="checksum mismatch at version 3"):
        Database(path)
