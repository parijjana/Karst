from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from src.karst_core.query import OperationalReadService


def seed_operational_views(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.executescript(
            """
            CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY, project_id INTEGER, file_id INTEGER,
                type TEXT, name TEXT, start_line INTEGER, end_line INTEGER
            );
            CREATE TABLE telemetry (
                id INTEGER PRIMARY KEY, project_id INTEGER, tool_name TEXT,
                latency_ms REAL, tokens_saved INTEGER, details TEXT, timestamp TEXT
            );
            CREATE TABLE commits (
                id INTEGER PRIMARY KEY, project_id INTEGER, commit_hash TEXT,
                message TEXT, timestamp TEXT
            );
            CREATE TABLE commit_files (
                id INTEGER PRIMARY KEY, commit_id INTEGER, file_path TEXT, status TEXT
            );
            INSERT INTO projects VALUES (1, 'one'), (2, 'two');
            INSERT INTO nodes VALUES
                (1, 1, 10, 'function', 'first', 1, 2),
                (2, 1, 10, 'class', 'second', 3, 5),
                (3, 2, 20, 'function', 'other', 1, 1);
            INSERT INTO telemetry VALUES
                (1, 1, 'query_symbol', 2.0, 4, NULL, '2026-01-01 10:15:00'),
                (2, 1, 'service:indexer', 4.0, 6, 'ok', '2026-01-01 11:15:00'),
                (3, 2, 'query_symbol', 6.0, 8, NULL, '2026-01-01 11:45:00'),
                (4, 1, 'service:parser', 3.0, 2, 'done', '2026-01-01 11:15:00');
            INSERT INTO commits VALUES
                (1, 1, 'older', 'old', '2026-01-01 10:00:00'),
                (2, 1, 'newer', 'new', '2026-01-01 11:00:00'),
                (3, 2, 'other', 'other', '2026-01-01 12:00:00');
            INSERT INTO commit_files VALUES
                (1, 1, 'old.py', 'M'), (2, 2, 'new.py', 'A');
            """
        )
        connection.commit()


def test_operational_views_preserve_payloads_ordering_and_pagination(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "karst.db"
    seed_operational_views(db_path)
    service = OperationalReadService(db_path)

    assert service.stats() == {
        "total_projects": 2,
        "total_nodes": 3,
        "queries_served": 4,
        "tokens_saved": 20,
    }
    assert service.nodes(1, 1, 1) == [
        {
            "id": 2,
            "file_id": 10,
            "type": "class",
            "name": "second",
            "start_line": 3,
            "end_line": 5,
        }
    ]
    assert service.project_telemetry(1, 2, 0) == [
        {
            "id": 4,
            "tool_name": "service:parser",
            "latency_ms": 3.0,
            "tokens_saved": 2,
            "timestamp": "2026-01-01 11:15:00",
        },
        {
            "id": 2,
            "tool_name": "service:indexer",
            "latency_ms": 4.0,
            "tokens_saved": 6,
            "timestamp": "2026-01-01 11:15:00",
        },
    ]
    assert service.commits(1, 1, 0) == [
        {
            "id": 2,
            "commit_hash": "newer",
            "message": "new",
            "timestamp": "2026-01-01 11:00:00",
            "files_changed": "A:new.py",
        }
    ]
    assert service.telemetry_aggregates(2, 0) == [
        {
            "time_bucket": "2026-01-01 11:00",
            "tool_name": "query_symbol",
            "calls": 1,
            "avg_latency": 6.0,
            "total_tokens": 8,
        },
        {
            "time_bucket": "2026-01-01 11:00",
            "tool_name": "service:indexer",
            "calls": 1,
            "avg_latency": 4.0,
            "total_tokens": 6,
        },
    ]
    assert service.service_metrics(2, 0) == [
        {
            "id": 4,
            "service": "service:parser",
            "latency_ms": 3.0,
            "processed_count": 2,
            "details": "done",
            "timestamp": "2026-01-01 11:15:00",
        },
        {
            "id": 2,
            "service": "service:indexer",
            "latency_ms": 4.0,
            "processed_count": 6,
            "details": "ok",
            "timestamp": "2026-01-01 11:15:00",
        },
    ]


def test_operational_views_have_honest_empty_and_missing_table_states(
    tmp_path: Path,
) -> None:
    missing = OperationalReadService(tmp_path / "missing.db")
    assert missing.stats() == {
        "total_projects": 0,
        "total_nodes": 0,
        "queries_served": 0,
        "tokens_saved": 0,
    }
    assert missing.nodes(1, 10, 0) == []
    assert missing.project_telemetry(1, 10, 0) == []
    assert missing.commits(1, 10, 0) == []
    assert missing.telemetry_aggregates(10, 0) == []
    assert missing.service_metrics(10, 0) == []

    legacy_path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(legacy_path)) as connection:
        connection.executescript(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT);"
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY, project_id INTEGER);"
        )
    legacy = OperationalReadService(legacy_path)
    assert legacy.stats()["queries_served"] == 0
    assert legacy.project_telemetry(1, 10, 0) == []
    assert legacy.commits(1, 10, 0) == []
    assert legacy.telemetry_aggregates(10, 0) == []
    assert legacy.service_metrics(10, 0) == []


def test_operational_read_uses_and_closes_a_read_only_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.karst_core.query import operational

    db_path = tmp_path / "karst.db"
    seed_operational_views(db_path)
    original_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []
    calls: list[tuple[str, bool]] = []

    def tracked_connect(database: str, *, uri: bool = False) -> sqlite3.Connection:
        calls.append((database, uri))
        connection = original_connect(database, uri=uri)
        opened.append(connection)
        return connection

    monkeypatch.setattr(operational.sqlite3, "connect", tracked_connect)
    OperationalReadService(db_path).stats()

    assert calls == [(f"file:{db_path}?mode=ro", True)]
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        opened[0].execute("SELECT 1")


def test_malformed_schema_error_propagates_and_closes_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.karst_core.query import operational

    db_path = tmp_path / "malformed.db"
    with closing(sqlite3.connect(db_path)) as connection:
        connection.executescript(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY);"
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY);"
            "CREATE TABLE telemetry (id INTEGER PRIMARY KEY);"
        )
    original_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []

    def tracked_connect(database: str, *, uri: bool = False) -> sqlite3.Connection:
        connection = original_connect(database, uri=uri)
        opened.append(connection)
        return connection

    monkeypatch.setattr(operational.sqlite3, "connect", tracked_connect)
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        OperationalReadService(db_path).stats()
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        opened[0].execute("SELECT 1")


def test_corrupt_database_error_is_not_converted_to_empty_state(tmp_path: Path) -> None:
    db_path = tmp_path / "corrupt.db"
    db_path.write_bytes(b"not a SQLite database")

    with pytest.raises(sqlite3.DatabaseError, match="not a database"):
        OperationalReadService(db_path).stats()
