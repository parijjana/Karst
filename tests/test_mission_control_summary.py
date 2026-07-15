from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from src.indexing_service import ProjectIndexService
from src.karst_core.database.database import Database
from src.karst_core.summary import ProjectSummaryService
from src.settings import Settings
from src.web import create_app
from tests.test_web_routes import ADMIN_TOKEN, CSRF_TOKEN, ORIGIN


def _settings(tmp_path: Path) -> Settings:
    (tmp_path / "data").mkdir()
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


def _index(settings: Settings, root: Path) -> None:
    service = ProjectIndexService(settings, lambda: Database(settings.db_path))
    assert service.index_project("demo", str(root)).startswith("Indexed 1 files")


def test_summary_persists_nonblank_loc_and_separates_inventory_from_health(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "module.py").write_text("\nvalue = 1\n\n# comment\n", encoding="utf-8")
    (project / "notes.txt").write_text("not parsed", encoding="utf-8")
    (project / "build").mkdir()
    (project / "build" / "bundle.js").write_text("generated", encoding="utf-8")
    settings = _settings(tmp_path)
    _index(settings, project)

    with Database(settings.db_path) as database:
        project_id = int(database.conn.execute("SELECT id FROM projects").fetchone()[0])
        database.conn.execute(
            "UPDATE index_generations SET indexed_files=0 WHERE project_id=? AND status='active'",
            (project_id,),
        )

    summary = ProjectSummaryService(settings.db_path).projects(10, 0)[0]
    assert summary["tracked_file_count"] == 1
    assert summary["nonblank_loc_total"] == 2
    assert summary["untracked_file_count"] == 1
    assert summary["untracked_folder_count"] == 1
    assert summary["discovered_not_indexed_count"] == 1
    assert ProjectSummaryService(settings.db_path).files(project_id, 10, 0) == [
        {"id": 1, "path": "module.py", "hash": summary_file_hash(settings), "nonblank_loc": 2}
    ]


def summary_file_hash(settings: Settings) -> str:
    with Database(settings.db_path) as database:
        return str(database.conn.execute("SELECT hash FROM files").fetchone()[0])


def test_changed_routes_use_core_summary_service_not_web_db_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (project / "module.py").write_text("value = 1\n", encoding="utf-8")
    _index(settings, project)

    def fail(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("changed endpoint constructed a web database connection")

    monkeypatch.setattr("src.web_data.get_db", fail)
    client = TestClient(create_app(settings), base_url=ORIGIN)
    assert client.get("/api/projects").status_code == 200
    assert client.get("/api/projects/1/files").status_code == 200
