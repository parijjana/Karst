from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.karst_core.database.database import Database
from src.karst_core.database.db_migrations import MigrationError


def test_quoted_current_timestamp_default_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "quoted-current-timestamp.db"
    with Database(path):
        pass

    connection = sqlite3.connect(path)
    connection.execute("ALTER TABLE active_processes RENAME TO original_processes")
    connection.execute(
        'CREATE TABLE active_processes ('
        'pid INTEGER PRIMARY KEY CHECK(pid > 0), '
        'script_name TEXT NOT NULL CHECK(length(script_name) > 0), '
        'last_heartbeat DATETIME NOT NULL DEFAULT "CURRENT_TIMESTAMP", '
        'last_status TEXT NOT NULL CHECK(length(last_status) > 0))'
    )
    connection.execute("DROP TABLE original_processes")
    connection.execute(
        "CREATE INDEX ix_active_processes_heartbeat "
        "ON active_processes(last_heartbeat)"
    )
    connection.execute(
        "INSERT INTO active_processes (pid, script_name, last_status) "
        "VALUES (1, 'worker', 'running')"
    )
    stored_default = connection.execute(
        "SELECT last_heartbeat FROM active_processes"
    ).fetchone()[0]
    connection.commit()
    connection.close()

    assert stored_default == "CURRENT_TIMESTAMP"
    with pytest.raises(
        MigrationError,
        match=r"default for active_processes\.last_heartbeat",
    ):
        Database(path)


def test_missing_required_default_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "missing-default.db"
    with Database(path):
        pass

    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute(
        "ALTER TABLE migration_audit RENAME TO original_migration_audit"
    )
    connection.execute(
        "CREATE TABLE migration_audit ("
        "migration_version INTEGER PRIMARY KEY CHECK(migration_version > 0), "
        "conflict_count INTEGER NOT NULL CHECK(conflict_count >= 0), "
        "details_json TEXT NOT NULL CHECK(length(details_json) > 0), "
        "applied_at DATETIME NOT NULL)"
    )
    connection.execute(
        "INSERT INTO migration_audit "
        "(migration_version, conflict_count, details_json, applied_at) "
        "SELECT migration_version, conflict_count, details_json, applied_at "
        "FROM original_migration_audit"
    )
    connection.execute("DROP TABLE original_migration_audit")
    connection.commit()
    connection.close()

    with pytest.raises(
        MigrationError,
        match=r"default for migration_audit\.applied_at",
    ):
        Database(path)
