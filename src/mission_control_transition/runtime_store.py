"""Local persistence for Mission Control transitional process supervision.

This module deliberately owns a database separate from Karst's graph database.
It is an additive, local implementation until Mission Control owns the runtime
service outright.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Protocol


DEFAULT_RUNTIME_DB_PATH = Path("data") / "mission_control_runtime.db"


class RuntimeStore(Protocol):
    """Persistence contract for transitional process supervision."""

    def register_process(self, pid: int, script_name: str, initial_status: str) -> None: ...

    def update_process_heartbeat(self, pid: int, status: str) -> None: ...

    def unregister_process(self, pid: int) -> None: ...

    def get_stale_processes(self, timeout_seconds: int) -> list[dict[str, Any]]: ...

    def record_event(
        self, event_type: str, pid: int, script_name: str, details: str
    ) -> None: ...

    def close(self) -> None: ...


class SQLiteRuntimeStore:
    """SQLite implementation of the Mission Control runtime-store contract."""

    def __init__(self, db_path: str | Path = DEFAULT_RUNTIME_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self._closed = False
        self._initialize()

    def _initialize(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_processes (
                pid INTEGER PRIMARY KEY,
                script_name TEXT NOT NULL,
                last_heartbeat TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_status TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_events (
                id INTEGER PRIMARY KEY,
                event_type TEXT NOT NULL,
                pid INTEGER NOT NULL,
                script_name TEXT NOT NULL,
                details TEXT NOT NULL,
                occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_runtime_processes_heartbeat "
            "ON runtime_processes(last_heartbeat)"
        )

    def register_process(self, pid: int, script_name: str, initial_status: str) -> None:
        self.conn.execute(
            "INSERT INTO runtime_processes "
            "(pid, script_name, last_heartbeat, last_status) "
            "VALUES (?, ?, CURRENT_TIMESTAMP, ?) "
            "ON CONFLICT(pid) DO UPDATE SET script_name = excluded.script_name, "
            "last_heartbeat = CURRENT_TIMESTAMP, last_status = excluded.last_status",
            (pid, script_name, initial_status),
        )

    def update_process_heartbeat(self, pid: int, status: str) -> None:
        self.conn.execute(
            "UPDATE runtime_processes SET last_heartbeat = CURRENT_TIMESTAMP, "
            "last_status = ? WHERE pid = ?",
            (status, pid),
        )

    def unregister_process(self, pid: int) -> None:
        self.conn.execute("DELETE FROM runtime_processes WHERE pid = ?", (pid,))

    def get_stale_processes(self, timeout_seconds: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT pid, script_name, last_status, "
            "(julianday('now') - julianday(last_heartbeat)) * 86400.0 "
            "AS elapsed_seconds FROM runtime_processes "
            "WHERE last_heartbeat < datetime('now', '-' || ? || ' seconds') "
            "ORDER BY last_heartbeat",
            (timeout_seconds,),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_event(
        self, event_type: str, pid: int, script_name: str, details: str
    ) -> None:
        self.conn.execute(
            "INSERT INTO runtime_events (event_type, pid, script_name, details) "
            "VALUES (?, ?, ?, ?)",
            (event_type, pid, script_name, details),
        )

    def close(self) -> None:
        if not self._closed:
            self.conn.close()
            self._closed = True

    def __enter__(self) -> SQLiteRuntimeStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def create_runtime_store(db_path: str | Path | None = None) -> RuntimeStore:
    """Create the transition-owned local store without consulting Karst settings."""
    return SQLiteRuntimeStore(DEFAULT_RUNTIME_DB_PATH if db_path is None else db_path)
