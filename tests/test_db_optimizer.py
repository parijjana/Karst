from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scripts import db_optimizer


class RecordingConnection:
    def __init__(self, failure_sql: str | None = None) -> None:
        self.failure_sql = failure_sql
        self.events: list[str] = []

    def execute(self, sql: str) -> None:
        normalized = " ".join(sql.split())
        self.events.append(normalized)
        if normalized == self.failure_sql:
            raise RuntimeError(f"failed: {normalized}")

    def commit(self) -> None:
        self.events.append("COMMIT")

    def rollback(self) -> None:
        self.events.append("ROLLBACK")


class RecordingDatabase:
    def __init__(self, connection: RecordingConnection) -> None:
        self.conn = connection
        self.closed = False

    def close(self) -> None:
        self.closed = True


def database_factory(
    connection: RecordingConnection,
) -> tuple[Callable[[str], RecordingDatabase], list[RecordingDatabase]]:
    databases: list[RecordingDatabase] = []

    def create(_: str) -> RecordingDatabase:
        database = RecordingDatabase(connection)
        databases.append(database)
        return database

    return create, databases


def test_optimize_database_commits_prune_before_vacuum_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = RecordingConnection()
    create, databases = database_factory(connection)
    monkeypatch.setattr(db_optimizer, "Database", create)

    db_optimizer.optimize_database(Path("database.db"))

    assert connection.events == [
        "DELETE FROM telemetry WHERE timestamp < datetime('now', '-30 days')",
        "COMMIT",
        "VACUUM",
        "ANALYZE",
        "COMMIT",
    ]
    assert databases[0].closed is True


@pytest.mark.parametrize("failure_sql", ["VACUUM", "ANALYZE"])
def test_optimize_database_rolls_back_and_closes_after_maintenance_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure_sql: str,
) -> None:
    connection = RecordingConnection(failure_sql)
    create, databases = database_factory(connection)
    monkeypatch.setattr(db_optimizer, "Database", create)

    with pytest.raises(RuntimeError, match=f"failed: {failure_sql}"):
        db_optimizer.optimize_database(Path("database.db"))

    assert connection.events[-1] == "ROLLBACK"
    assert databases[0].closed is True


def test_log_optimization_closes_database_when_telemetry_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingTelemetryDatabase(RecordingDatabase):
        def log_telemetry(self, *args: Any, **kwargs: Any) -> int:
            raise RuntimeError("telemetry failed")

    database = FailingTelemetryDatabase(RecordingConnection())
    monkeypatch.setattr(db_optimizer, "Database", lambda _: database)

    with pytest.raises(RuntimeError, match="telemetry failed"):
        db_optimizer.log_optimization(Path("database.db"), 10.0, 2.0)

    assert database.closed is True


def test_real_sqlite_maintenance_prunes_analyzes_and_releases_file(
) -> None:
    unique_name = uuid.uuid4().hex
    db_path = Path(f".test-db-optimizer-{unique_name}.db")
    released_path = Path(f".test-db-optimizer-{unique_name}-released.db")
    try:
        seed = db_optimizer.Database(str(db_path))
        try:
            seed.conn.execute(
                "INSERT INTO telemetry (tool_name, latency_ms, timestamp) "
                "VALUES (?, ?, ?)",
                ("old", 0.0, "2000-01-01 00:00:00"),
            )
            seed.conn.execute(
                "INSERT INTO telemetry (tool_name, latency_ms, timestamp) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                ("recent", 0.0),
            )
            seed.conn.execute(
                "CREATE INDEX ix_test_telemetry_tool_name ON telemetry(tool_name)"
            )
            seed.conn.commit()
        finally:
            seed.close()

        # A successful real VACUUM after the DELETE proves the prune transaction was
        # committed first; SQLite rejects VACUUM while a transaction is active.
        db_optimizer.optimize_database(db_path)

        verification = sqlite3.connect(db_path)
        try:
            tool_names = [
                row[0]
                for row in verification.execute(
                    "SELECT tool_name FROM telemetry ORDER BY tool_name"
                ).fetchall()
            ]
            analyzed_rows = verification.execute(
                "SELECT COUNT(*) FROM sqlite_stat1 "
                "WHERE idx = 'ix_test_telemetry_tool_name'"
            ).fetchone()
        finally:
            verification.close()

        assert tool_names == ["recent"]
        assert analyzed_rows is not None
        assert analyzed_rows[0] == 1

        db_path.rename(released_path)
        assert released_path.is_file()
    finally:
        for path in (db_path, released_path):
            path.unlink(missing_ok=True)
