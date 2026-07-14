from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from src.web import create_app
from tests.test_admin_session_security import ORIGIN, make_settings


def test_file_seed_is_committed_closed_and_server_pagination_is_capped(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        tmp_path,
        dashboard_default_page_size=2,
        dashboard_max_page_size=3,
    )
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(settings.db_path)) as connection:
        connection.execute(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT, path TEXT)"
        )
        connection.execute(
            "CREATE TABLE files "
            "(id INTEGER PRIMARY KEY, project_id INTEGER, path TEXT, hash TEXT)"
        )
        connection.execute(
            "INSERT INTO projects (id, name, path) "
            "VALUES (1, 'project', '/project')"
        )
        connection.executemany(
            "INSERT INTO files (project_id, path, hash) VALUES (1, ?, ?)",
            [(f"/project/{index}.py", str(index)) for index in range(6)],
        )
        connection.commit()

    with closing(sqlite3.connect(settings.db_path)) as verification:
        assert verification.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 6

    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        page = client.get("/api/projects/1/files?limit=2&offset=1")
        oversized = client.get("/api/projects/1/files?limit=4")

    assert page.status_code == 200
    assert [row["path"] for row in page.json()] == [
        "/project/1.py",
        "/project/2.py",
    ]
    assert oversized.status_code == 422
