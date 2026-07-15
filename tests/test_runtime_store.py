from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src import watchdog_client
from src.mission_control_transition.runtime_store import SQLiteRuntimeStore


def test_runtime_store_tracks_stale_processes_and_termination_events(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime.db"
    with SQLiteRuntimeStore(path) as store:
        store.register_process(101, "worker", "started")
        store.update_process_heartbeat(101, "waiting")
        store.conn.execute(
            "UPDATE runtime_processes SET last_heartbeat = datetime('now', '-61 seconds')"
        )

        assert store.get_stale_processes(60) == [
            {
                "pid": 101,
                "script_name": "worker",
                "last_status": "waiting",
                "elapsed_seconds": pytest.approx(61.0, abs=2.0),
            }
        ]
        store.record_event("watchdog_termination", 101, "worker", "stale")
        store.unregister_process(101)

        event = store.conn.execute(
            "SELECT event_type, pid, script_name, details FROM runtime_events"
        ).fetchone()
        assert tuple(event) == ("watchdog_termination", 101, "worker", "stale")
        assert store.get_stale_processes(0) == []


def test_runtime_store_never_uses_karst_database_path(tmp_path: Path) -> None:
    karst_path = tmp_path / "knowledge_graph.db"
    with sqlite3.connect(karst_path) as connection:
        connection.execute("CREATE TABLE active_processes (pid INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO active_processes VALUES (999)")

    runtime_path = tmp_path / "mission_control_runtime.db"
    with SQLiteRuntimeStore(runtime_path) as store:
        store.register_process(101, "worker", "started")

    with sqlite3.connect(karst_path) as connection:
        assert connection.execute("SELECT pid FROM active_processes").fetchall() == [(999,)]
    assert runtime_path.exists()


def test_watchdog_client_uses_transition_store_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int, str]] = []

    class Store:
        def register_process(self, pid: int, script_name: str, status: str) -> None:
            calls.append(("register", pid, status))

        def update_process_heartbeat(self, pid: int, status: str) -> None:
            calls.append(("heartbeat", pid, status))

        def unregister_process(self, pid: int) -> None:
            calls.append(("unregister", pid, ""))

        def close(self) -> None:
            calls.append(("close", 0, ""))

    monkeypatch.setattr(watchdog_client, "create_runtime_store", lambda _: Store())
    monkeypatch.setattr(watchdog_client.os, "getpid", lambda: 101)

    with watchdog_client.managed_process("worker") as client:
        client.update_progress("indexing")

    assert calls == [
        ("register", 101, "Started"),
        ("heartbeat", 101, "indexing"),
        ("unregister", 101, ""),
        ("close", 0, ""),
    ]


def test_watchdog_modules_do_not_import_karst_database() -> None:
    root = Path(__file__).parents[1]
    for path in (root / "src" / "watchdog_client.py", root / "scripts" / "watchdog.py"):
        assert "src.database" not in path.read_text(encoding="utf-8")
