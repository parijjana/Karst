from __future__ import annotations

import sqlite3
from typing import Any


class ProcessRepositoryMixin:
    """Persistence operations for the temporary process-heartbeat subsystem."""

    conn: sqlite3.Connection

    def _before_write(self) -> None:
        raise NotImplementedError

    def _ensure_open(self) -> None:
        raise NotImplementedError

    def _auto_commit(self) -> None:
        raise NotImplementedError

    def register_process(self, pid: int, script_name: str, initial_status: str) -> None:
        self._before_write()
        self.conn.execute(
            "INSERT INTO active_processes "
            "(pid, script_name, last_heartbeat, last_status) "
            "VALUES (?, ?, CURRENT_TIMESTAMP, ?) "
            "ON CONFLICT(pid) DO UPDATE SET script_name = excluded.script_name, "
            "last_heartbeat = CURRENT_TIMESTAMP, last_status = excluded.last_status",
            (pid, script_name, initial_status),
        )
        self._auto_commit()

    def update_process_heartbeat(self, pid: int, status: str) -> None:
        self._before_write()
        self.conn.execute(
            "UPDATE active_processes SET last_heartbeat = CURRENT_TIMESTAMP, "
            "last_status = ? WHERE pid = ?",
            (status, pid),
        )
        self._auto_commit()

    def unregister_process(self, pid: int) -> None:
        self._before_write()
        self.conn.execute("DELETE FROM active_processes WHERE pid = ?", (pid,))
        self._auto_commit()

    def get_stale_processes(self, timeout_seconds: int) -> list[dict[str, Any]]:
        self._ensure_open()
        rows = self.conn.execute(
            "SELECT pid, script_name, last_status, "
            "(julianday('now') - julianday(last_heartbeat)) * 86400.0 "
            "AS elapsed_seconds FROM active_processes "
            "WHERE last_heartbeat < datetime('now', '-' || ? || ' seconds') "
            "ORDER BY last_heartbeat",
            (timeout_seconds,),
        ).fetchall()
        return [dict(row) for row in rows]
