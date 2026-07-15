from __future__ import annotations

from pathlib import Path

import pytest

from scripts.reindexer import reindex_projects
from src import main
from src.settings import Settings


def test_reindexer_uses_configured_database_identity_and_parse_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    elsewhere = tmp_path / "elsewhere"
    project.mkdir()
    elsewhere.mkdir()
    (project / "good.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "bad.py").write_text("def bad(:\n", encoding="utf-8")
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "karst.db",
        allowed_roots=(tmp_path,),
    )
    monkeypatch.setattr(main, "core_settings", settings)
    first_result = main.index_project("project", str(project))
    assert "failed 1" in first_result
    monkeypatch.chdir(elsewhere)

    reports = reindex_projects(settings)

    assert len(reports) == 1
    assert reports[0].project_name == "project"
    assert reports[0].indexed_count == 1
    assert reports[0].failed_count == 1
    assert reports[0].skipped_count == 0
    db = main.get_db(settings)
    try:
        row = db.conn.execute(
            "SELECT path, owner, stable_id FROM projects WHERE name = 'project'"
        ).fetchone()
    finally:
        db.close()
    assert row is not None
    assert Path(row[0]) == project.resolve()
    assert row[1] == "local-stdio"
    assert row[2]
