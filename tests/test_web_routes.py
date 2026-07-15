from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.karst_core.database.database import Database
from src.settings import Settings, TRUSTED_LOCAL_OWNER
from src.web import create_app


ORIGIN = "http://127.0.0.1:8085"
ADMIN_TOKEN = "a" * 32
CSRF_TOKEN = "c" * 32


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "karst.db",
        allowed_roots=(tmp_path,),
        allowed_hosts=("127.0.0.1",),
        allowed_origins=(ORIGIN,),
        admin_token=ADMIN_TOKEN,
        csrf_token=CSRF_TOKEN,
        dashboard_default_page_size=10,
        dashboard_max_page_size=20,
    )


def seed_database(settings: Settings, tmp_path: Path) -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with Database(settings.db_path) as database:
        project_id = database.add_project(
            "demo", str(tmp_path), TRUSTED_LOCAL_OWNER, "test:demo"
        )
        file_id = database.add_file(project_id, str(tmp_path / "module.py"), "hash")
        first = database.add_node(project_id, file_id, "function", "first", 1, 2)
        second = database.add_node(project_id, file_id, "function", "second", 4, 5)
        database.add_edge(project_id, first, second, "calls")
        database.log_telemetry(project_id, "query_symbol", 2.5, 8)
        database.log_telemetry(project_id, "service:indexer", 3.5, 2, "ok")
        database.log_commit(
            project_id, "abc", "message", [{"path": "module.py", "status": "M"}]
        )


def test_read_routes_return_paginated_database_views_and_closed_connections(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    seed_database(settings, tmp_path)
    client = TestClient(create_app(settings), base_url=ORIGIN)

    assert client.get("/api/stats").json() == {
        "total_projects": 1,
        "total_nodes": 2,
        "queries_served": 2,
        "tokens_saved": 10,
    }
    assert client.get("/api/projects").json()[0]["name"] == "demo"
    file_row = client.get("/api/projects/1/files").json()[0]
    assert file_row["hash"] == "hash"
    assert file_row["path"] == "module.py"
    assert file_row["nonblank_loc"] == 0
    assert len(client.get("/api/projects/1/nodes").json()) == 2
    assert len(client.get("/api/projects/1/telemetry").json()) == 2
    assert client.get("/api/projects/1/commits").json()[0]["commit_hash"] == "abc"
    assert client.get("/api/telemetry").json()
    assert client.get("/api/services/metrics").json()[0]["service"] == "service:indexer"

    graph = client.get("/api/graph?project_id=1").json()
    node_ids = {node["id"] for node in graph["nodes"]}
    assert {"project_1", "file_1", "node_1", "node_2"} <= node_ids
    assert all(
        link["source"] in node_ids and link["target"] in node_ids
        for link in graph["links"]
    )

    with closing(sqlite3.connect(settings.db_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1


def test_read_routes_have_honest_empty_states(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings), base_url=ORIGIN)

    assert client.get("/api/projects").json() == []
    assert client.get("/api/projects/1/files").json() == []
    assert client.get("/api/projects/1/nodes").json() == []
    assert client.get("/api/projects/1/telemetry").json() == []
    assert client.get("/api/projects/1/commits").json() == []
    assert client.get("/api/telemetry").json() == []
    assert client.get("/api/services/metrics").json() == []
    assert client.get("/api/graph").json() == {"nodes": [], "links": []}


def test_admin_process_routes_map_unknown_scripts_to_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def invalid(_script_name: str) -> object:
        raise ValueError("unknown")

    monkeypatch.setattr("src.web.start_script", invalid)
    monkeypatch.setattr("src.web.stop_script", invalid)
    client = TestClient(create_app(make_settings(tmp_path)), base_url=ORIGIN)
    headers = {
        "Authorization": f"Bearer {ADMIN_TOKEN}",
        "X-CSRF-Token": CSRF_TOKEN,
    }

    assert (
        client.post("/api/admin/scripts/unknown/start", headers=headers).status_code
        == 404
    )
    assert (
        client.post("/api/admin/scripts/unknown/stop", headers=headers).status_code
        == 404
    )
    assert client.get("/api/admin/scripts/unknown/status").json() == {
        "status": "invalid_script"
    }


def test_application_lifespan_cleans_up_managed_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def fake_shutdown() -> None:
        calls.append("shutdown")

    monkeypatch.setattr("src.web.shutdown_all_scripts", fake_shutdown)

    with TestClient(create_app(make_settings(tmp_path)), base_url=ORIGIN) as client:
        assert client.get("/api/stats").status_code == 200

    assert calls == ["shutdown"]
