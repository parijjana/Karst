from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.database import Database
from src.db_migrations import (
    CURRENT_SCHEMA_VERSION,
    Migration,
    MigrationError,
    migrate,
)


def _create_legacy_schema(path: Path, *, wave_one_identity: bool) -> None:
    connection = sqlite3.connect(path)
    identity_columns = (
        ", owner TEXT NOT NULL DEFAULT 'local-stdio', stable_id TEXT"
        if wave_one_identity
        else ""
    )
    connection.executescript(
        f"""
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            path TEXT{identity_columns}
        );
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            project_id INTEGER,
            path TEXT,
            hash TEXT,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE TABLE nodes (
            id INTEGER PRIMARY KEY,
            project_id INTEGER,
            file_id INTEGER,
            type TEXT,
            name TEXT,
            start_line INTEGER,
            end_line INTEGER,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY,
            project_id INTEGER,
            source_id INTEGER,
            target_id INTEGER,
            type TEXT,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(source_id) REFERENCES nodes(id) ON DELETE CASCADE,
            FOREIGN KEY(target_id) REFERENCES nodes(id) ON DELETE CASCADE
        );
        CREATE TABLE telemetry (
            id INTEGER PRIMARY KEY,
            project_id INTEGER,
            tool_name TEXT,
            latency_ms REAL,
            tokens_saved INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE TABLE commits (
            id INTEGER PRIMARY KEY,
            project_id INTEGER,
            commit_hash TEXT,
            message TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE TABLE commit_files (
            id INTEGER PRIMARY KEY,
            commit_id INTEGER,
            file_path TEXT,
            status TEXT,
            FOREIGN KEY(commit_id) REFERENCES commits(id) ON DELETE CASCADE
        );
        CREATE TABLE active_processes (
            pid INTEGER PRIMARY KEY,
            script_name TEXT,
            last_heartbeat DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_status TEXT
        );
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY,
            node_id INTEGER,
            vector TEXT,
            FOREIGN KEY(node_id) REFERENCES nodes(id) ON DELETE CASCADE
        );
        """
    )
    if wave_one_identity:
        connection.execute(
            "INSERT INTO projects "
            "(id, name, path, owner, stable_id) VALUES (1, ?, ?, ?, ?)",
            ("legacy", "/legacy/project", "local-stdio", None),
        )
    else:
        connection.execute(
            "INSERT INTO projects (id, name, path) VALUES (1, ?, ?)",
            ("legacy", "/legacy/project"),
        )
    connection.execute(
        "INSERT INTO files VALUES (1, 1, '/legacy/project/a.py', 'hash')"
    )
    connection.execute(
        "INSERT INTO nodes VALUES (1, 1, 1, 'function', 'run', 1, 2)"
    )
    connection.execute(
        "INSERT INTO commits "
        "(id, project_id, commit_hash, message) VALUES (1, 1, 'abc', 'first')"
    )
    connection.execute(
        "INSERT INTO commit_files VALUES (1, 1, 'a.py', 'M')"
    )
    connection.execute("INSERT INTO embeddings VALUES (1, 1, '[0.1, 0.2]')")
    connection.commit()
    connection.close()


def test_fresh_database_has_current_version_and_core_schema(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"

    with Database(path) as database:
        tables = {
            row[0]
            for row in database.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        applied = database.conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()

        assert database.schema_version == CURRENT_SCHEMA_VERSION
        assert {"projects", "nodes", "edges", "commits", "embeddings"} <= tables
        assert [row[0] for row in applied] == list(
            range(1, CURRENT_SCHEMA_VERSION + 1)
        )
        assert database.integrity_report().ok


@pytest.mark.parametrize("wave_one_identity", [False, True])
def test_representative_legacy_database_upgrades_without_forging_identity(
    tmp_path: Path, wave_one_identity: bool
) -> None:
    path = tmp_path / f"legacy-{wave_one_identity}.db"
    _create_legacy_schema(path, wave_one_identity=wave_one_identity)

    with Database(path) as database:
        project = database.conn.execute(
            "SELECT name, path, owner, stable_id FROM projects"
        ).fetchone()
        counts = tuple(
            database.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("files", "nodes", "commits", "commit_files", "embeddings")
        )

        assert database.schema_version == CURRENT_SCHEMA_VERSION
        assert tuple(project) == (
            "legacy",
            "/legacy/project",
            "local-stdio" if wave_one_identity else None,
            None,
        )
        assert counts == (1, 1, 1, 1, 1)
        assert database.integrity_report().ok


def test_reopening_current_database_is_migration_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "repeat.db"
    with Database(path) as database:
        before = database.conn.execute(
            "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()

    with Database(path) as database:
        after = database.conn.execute(
            "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()

    assert [tuple(row) for row in after] == [tuple(row) for row in before]


def test_failed_migration_rolls_back_all_pending_versions_and_connection_stays_usable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rollback.db"
    connection = sqlite3.connect(path)

    def create_marker(target: sqlite3.Connection) -> None:
        target.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        target.execute("INSERT INTO marker VALUES ('preserved')")

    def fail_after_write(target: sqlite3.Connection) -> None:
        target.execute("ALTER TABLE marker ADD COLUMN transient TEXT")
        target.execute("UPDATE marker SET transient = 'must rollback'")
        raise RuntimeError("injected migration failure")

    migrations = (
        Migration(1, "create marker", create_marker),
        Migration(2, "fail marker", fail_after_write),
    )

    with pytest.raises(MigrationError, match="fail marker"):
        migrate(connection, migrations=migrations)

    assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name = 'marker'"
    ).fetchone()[0] == 0
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()


def test_future_schema_version_is_rejected_without_mutation(tmp_path: Path) -> None:
    path = tmp_path / "future.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE marker (value TEXT)")
    connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION + 1}")
    connection.commit()
    connection.close()

    with pytest.raises(MigrationError, match="newer than this Karst build"):
        Database(path)

    verification = sqlite3.connect(path)
    assert verification.execute("SELECT COUNT(*) FROM marker").fetchone()[0] == 0
    assert verification.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    verification.close()


def test_migration_callables_are_ordered_and_contiguous(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "gap.db")

    def no_op(_connection: sqlite3.Connection) -> None:
        return None

    with pytest.raises(MigrationError, match="contiguous"):
        migrate(
            connection,
            migrations=(Migration(1, "first", no_op), Migration(3, "gap", no_op)),
        )
    connection.close()
