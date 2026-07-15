from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
import json

from src import main
from src.karst_core.database.database import DatabaseMigrationRecoveryRequired
from tests.database_v2_generation_support import create_v2_database
from tests.main_support import configured_settings, seed_populated_legacy_generation


def test_mcp_server_name_and_public_tool_contract() -> None:
    assert main.mcp.name == "Karst"
    expected = {
        "index_project",
        "update_graph",
        "query_symbol",
        "get_file_outline",
        "find_dependencies",
        "find_dependents",
        "log_commit",
        "backfill_git_history",
        "semantic_search",
        "rebuild_database",
    }

    registered = set(main.mcp._tool_manager._tools)

    assert expected <= registered
    assert "list_symbols" in registered


def test_legacy_relative_path_database_requires_explicit_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = configured_settings(tmp_path)
    configuration.data_dir.mkdir(parents=True)
    create_v2_database(
        configuration.db_path,
        project_root="legacy/project",
        file_path="legacy/project/a.py",
    )
    monkeypatch.setattr(main, "core_settings", configuration)

    with pytest.raises(DatabaseMigrationRecoveryRequired, match="No data was deleted"):
        main.query_symbol("missing", "name")

    with closing(sqlite3.connect(configuration.db_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 2

    assert "Rebuild rejected" in main.rebuild_database("no")
    assert main.rebuild_database("DELETE_AND_REBUILD") == (
        "Karst database deleted and rebuilt with the current schema."
    )
    assert main.query_symbol("missing", "name") == "Project not found."
    assert "only available" in main.rebuild_database("DELETE_AND_REBUILD")
    with closing(sqlite3.connect(configuration.db_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


def test_list_symbols_missing_project_uses_v1_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "core_settings", configured_settings(tmp_path))
    payload = json.loads(main.list_symbols("missing"))
    assert payload["schema_version"] == "v1"
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "project_not_found"


def test_list_symbols_invalid_limit_uses_v1_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = configured_settings(tmp_path)
    monkeypatch.setattr(main, "core_settings", configuration)
    main.index_project("demo", str(tmp_path))
    payload = json.loads(main.list_symbols("demo", limit=0))
    assert payload["schema_version"] == "v1"
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "limit_exceeded"


def test_mcp_index_and_update_promote_query_ready_generations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "module.py"
    source.write_text("def first_symbol():\n    return 1\n", encoding="utf-8")
    configuration = configured_settings(tmp_path)
    monkeypatch.setattr(main, "core_settings", configuration)

    assert main.index_project("demo", str(project)).startswith("Indexed 1 files")
    listed = json.loads(main.list_symbols("demo", name="first_symbol"))
    assert listed["status"] == "success"
    assert main.query_symbol("demo", "first_symbol").startswith(
        "Symbol 'first_symbol'"
    )

    source.write_text("def second_symbol():\n    return 2\n", encoding="utf-8")
    assert main.update_graph("demo", [str(source)]).startswith("Updated 1 files")
    updated = json.loads(main.list_symbols("demo", name="second_symbol"))
    assert updated["status"] == "success"
    assert main.query_symbol("demo", "second_symbol").startswith(
        "Symbol 'second_symbol'"
    )

    with closing(sqlite3.connect(configuration.db_path)) as connection:
        assert connection.execute(
            "SELECT query_ready FROM index_generations "
            "WHERE project_id = 1 AND status = 'active'"
        ).fetchone()[0] == 1


def test_mcp_index_replaces_populated_legacy_nonready_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    contents = b"def legacy_symbol():\n    return 1\n"
    configuration = configured_settings(tmp_path)
    monkeypatch.setattr(main, "core_settings", configuration)

    with main.get_db(configuration) as database:
        project_id = seed_populated_legacy_generation(
            database,
            "legacy",
            project,
            {"module.py": (contents, "legacy_symbol")},
        )

    assert main.index_project("legacy", str(project)).startswith("Indexed 1 files")
    listed = json.loads(main.list_symbols("legacy", name="legacy_symbol"))
    assert listed["status"] == "success"
    assert main.query_symbol("legacy", "legacy_symbol").startswith(
        "Symbol 'legacy_symbol'"
    )
    with closing(sqlite3.connect(configuration.db_path)) as connection:
        assert connection.execute(
            "SELECT query_ready FROM index_generations "
            "WHERE project_id = ? AND status = 'active'",
            (project_id,),
        ).fetchone()[0] == 1


def test_mcp_index_rebuilds_populated_legacy_nonready_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sources = {
        "first.py": b"def first_symbol():\n    return 1\n",
        "second.py": b"def second_symbol():\n    return 2\n",
    }
    configuration = configured_settings(tmp_path)
    monkeypatch.setattr(main, "core_settings", configuration)

    with main.get_db(configuration) as database:
        project_id = seed_populated_legacy_generation(
            database,
            "unchanged",
            project,
            {
                name: (contents, f"{Path(name).stem}_symbol")
                for name, contents in sources.items()
            },
        )

    assert main.index_project("unchanged", str(project)).startswith("Indexed 2 files")
    listed = json.loads(main.list_symbols("unchanged", name="first_symbol"))
    assert listed["status"] == "success"
    with closing(sqlite3.connect(configuration.db_path)) as connection:
        generation = connection.execute(
            "SELECT query_ready, discovered_files, indexed_files FROM index_generations "
            "WHERE project_id = ? AND status = 'active'",
            (project_id,),
        ).fetchone()
    assert tuple(generation) == (1, 2, 2)


def test_run_uses_injected_transport_runner() -> None:
    calls: list[str] = []

    main.run(lambda: calls.append("ran"))

    assert calls == ["ran"]


def test_symbol_outline_dependency_and_commit_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "module.py"
    source.write_text(
        "def source():\n    return target()\n\ndef target():\n    return 1\n",
        encoding="utf-8",
    )
    configuration = configured_settings(tmp_path)
    monkeypatch.setattr(main, "core_settings", configuration)
    assert main.index_project("demo", str(project)).startswith("Indexed 1 files")

    with closing(sqlite3.connect(configuration.db_path)) as connection:
        generation_id = connection.execute(
            "SELECT id FROM index_generations "
            "WHERE project_id = 1 AND status = 'active'"
        ).fetchone()[0]
        source_id = connection.execute(
            "SELECT id FROM nodes WHERE name = 'source'"
        ).fetchone()[0]
        target_id = connection.execute(
            "SELECT id FROM nodes WHERE name = 'target'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO edges "
            "(project_id, generation_id, source_id, target_id, type) "
            "VALUES (1, ?, ?, ?, 'calls')",
            (generation_id, source_id, target_id),
        )
        connection.commit()

    assert "defined in" in main.query_symbol("demo", "source")
    assert "function source" in main.get_file_outline("demo", str(source))
    assert "target (function) [edge: calls]" in main.find_dependencies("demo", "source")
    assert "source (function) [edge: calls]" in main.find_dependents("demo", "target")
    assert (
        main.log_commit(
            "demo", "abc123", "message", [{"path": "module.py", "status": "M"}]
        )
        == "Logged commit abc123 for project 'demo'."
    )


def test_tool_errors_close_connections_and_remain_transport_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configuration = configured_settings(tmp_path)
    monkeypatch.setattr(main, "core_settings", configuration)

    assert main.query_symbol("missing", "name") == "Project not found."
    assert main.get_file_outline("missing", "missing.py") == "Project not found."
    assert main.find_dependencies("missing", "name") == "Project not found."
    assert main.find_dependents("missing", "name") == "Project not found."
    assert main.log_commit("missing", "hash", "message", []) == "Project not found."
    assert main.semantic_search("missing", "query") == "Project not found."
    assert main.backfill_git_history("missing") == "Project not found."
    assert capsys.readouterr().out == ""


def test_backfill_and_semantic_adapters_use_validated_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    configuration = configured_settings(tmp_path)
    monkeypatch.setattr(main, "core_settings", configuration)
    assert main.index_project("demo", str(project)).startswith("Indexed")
    observed: dict[str, object] = {}

    def fake_backfill(
        db: object,
        project_id: int,
        project_name: str,
        project_path: str,
        limit: int,
    ) -> str:
        observed["backfill"] = (project_id, project_name, project_path, limit)
        return "backfilled"

    def fake_search(
        db: object, project_id: int, query: str, limit: int
    ) -> tuple[str, float, int]:
        observed["search"] = (project_id, query, limit)
        return "searched", 0.01, 4

    monkeypatch.setattr(main, "do_backfill_git_history", fake_backfill)
    monkeypatch.setattr(main, "do_semantic_search", fake_search)

    assert main.backfill_git_history("demo", 7) == "backfilled"
    assert main.semantic_search("demo", "needle", 3) == "searched"
    assert observed == {
        "backfill": (1, "demo", str(project.resolve()), 7),
        "search": (1, "needle", 3),
    }
