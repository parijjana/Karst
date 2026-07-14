from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from src.database import Database
from src.db_migrations import CURRENT_SCHEMA_VERSION, MigrationError


def _rebuild_table(
    path: Path,
    table: str,
    definition: str,
    *,
    copy_columns: str | None = None,
    indexes: tuple[str, ...] = (),
) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = OFF")
    original = f"{table}_original"
    connection.execute(f"ALTER TABLE {table} RENAME TO {original}")
    connection.execute(definition)
    if copy_columns is not None:
        connection.execute(
            f"INSERT INTO {table} ({copy_columns}) "
            f"SELECT {copy_columns} FROM {original}"
        )
    connection.execute(f"DROP TABLE {original}")
    for statement in indexes:
        connection.execute(statement)
    return connection


def test_tampered_migration_checksum_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    with Database(path):
        pass
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 2"
    )
    connection.commit()
    connection.close()

    with pytest.raises(MigrationError, match="checksum"):
        Database(path)


def test_current_version_with_missing_schema_shape_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "shape.db"
    with Database(path):
        pass
    connection = sqlite3.connect(path)
    connection.execute("DROP INDEX ix_edges_source")
    connection.commit()
    connection.close()

    with pytest.raises(MigrationError, match="schema shape"):
        Database(path)


def test_future_version_rejection_does_not_change_persistent_pragmas_or_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "future-pragmas.db"
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0] == "delete"
    connection.execute("CREATE TABLE marker (value TEXT)")
    connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION + 1}")
    connection.commit()
    connection.close()

    with pytest.raises(MigrationError, match="newer than this Karst build"):
        Database(path)

    verification = sqlite3.connect(path)
    assert verification.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert verification.execute("PRAGMA user_version").fetchone()[0] == (
        CURRENT_SCHEMA_VERSION + 1
    )
    assert (
        verification.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'schema_migrations'"
        ).fetchone()[0]
        == 0
    )
    verification.close()


def test_schema_contains_no_redundant_legacy_indexes(tmp_path: Path) -> None:
    with Database(tmp_path / "indexes.db") as database:
        indexes = {
            row[0]
            for row in database.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }

    assert {
        "ix_files_project",
        "ix_nodes_file_type",
        "ix_edges_project",
        "ix_commit_files_commit",
    }.isdisjoint(indexes)


def test_wrong_partial_index_predicate_cannot_hide_duplicate_stable_ids(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wrong-partial-predicate.db"
    with Database(path):
        pass
    connection = sqlite3.connect(path)
    connection.execute("DROP INDEX ux_projects_stable_id")
    connection.execute(
        "CREATE UNIQUE INDEX ux_projects_stable_id ON projects(stable_id) "
        "WHERE stable_id IS NULL"
    )
    connection.execute(
        "INSERT INTO projects (name, path, owner, stable_id) VALUES (?, ?, ?, ?)",
        ("first", "/first", "local-stdio", "duplicate-stable-id"),
    )
    connection.execute(
        "INSERT INTO projects (name, path, owner, stable_id) VALUES (?, ?, ?, ?)",
        ("second", "/second", "local-stdio", "duplicate-stable-id"),
    )
    connection.commit()
    connection.close()

    with pytest.raises(MigrationError, match="partial-index predicate"):
        Database(path)


def test_separate_foreign_keys_cannot_masquerade_as_composite_relationships(
    tmp_path: Path,
) -> None:
    path = tmp_path / "foreign-key-grouping.db"
    with Database(path):
        pass
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("DROP TABLE edges")
    connection.execute(
        """
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            type TEXT NOT NULL CHECK(length(type) > 0),
            FOREIGN KEY(project_id) REFERENCES nodes(project_id) ON DELETE CASCADE,
            FOREIGN KEY(project_id) REFERENCES nodes(project_id) ON DELETE CASCADE,
            FOREIGN KEY(source_id) REFERENCES nodes(id) ON DELETE CASCADE,
            FOREIGN KEY(target_id) REFERENCES nodes(id) ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        "CREATE UNIQUE INDEX ux_edges_identity "
        "ON edges(project_id, source_id, target_id, type)"
    )
    connection.execute("CREATE INDEX ix_edges_source ON edges(source_id)")
    connection.execute("CREATE INDEX ix_edges_target ON edges(target_id)")
    connection.commit()
    connection.close()

    with pytest.raises(MigrationError, match="edges"):
        Database(path)


def test_initialization_lock_is_acquired_before_version_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "lock-order.db"
    events: list[str] = []
    lock = threading.Lock()
    real_connect = sqlite3.connect

    class RecordingConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
            normalized = " ".join(sql.split()).upper()
            if normalized.startswith("BEGIN IMMEDIATE"):
                with lock:
                    events.append("begin")
            elif normalized.startswith("PRAGMA USER_VERSION"):
                with lock:
                    events.append("version")
            return super().execute(sql, parameters)

    def recording_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        kwargs["factory"] = RecordingConnection
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", recording_connect)
    with Database(path):
        pass

    assert events.index("begin") < events.index("version")


def test_current_schema_rejects_table_with_missing_check_constraints(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing-checks.db"
    with Database(path):
        pass
    connection = _rebuild_table(
        path,
        "active_processes",
        "CREATE TABLE active_processes ("
        "pid INTEGER PRIMARY KEY, script_name TEXT NOT NULL, "
        "last_heartbeat DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "last_status TEXT NOT NULL)",
        indexes=(
            "CREATE INDEX ix_active_processes_heartbeat "
            "ON active_processes(last_heartbeat)",
        ),
    )
    connection.execute(
        "INSERT INTO active_processes (pid, script_name, last_status) "
        "VALUES (-1, '', '')"
    )
    connection.commit()
    connection.close()

    with pytest.raises(MigrationError, match="table definition for active_processes"):
        Database(path)


def test_current_schema_rejects_missing_check_on_another_managed_table(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing-audit-check.db"
    with Database(path):
        pass
    connection = _rebuild_table(
        path,
        "migration_audit",
        "CREATE TABLE migration_audit ("
        "migration_version INTEGER PRIMARY KEY CHECK(migration_version > 0), "
        "conflict_count INTEGER NOT NULL, "
        "details_json TEXT NOT NULL CHECK(length(details_json) > 0), "
        "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        copy_columns=("migration_version, conflict_count, details_json, applied_at"),
    )
    connection.execute(
        "INSERT INTO migration_audit "
        "(migration_version, conflict_count, details_json) VALUES (99, -1, '{}')"
    )
    connection.commit()
    connection.close()

    with pytest.raises(MigrationError, match="table definition for migration_audit"):
        Database(path)
