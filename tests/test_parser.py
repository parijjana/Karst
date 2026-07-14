from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from src import parser as parser_module
from src.database import Database
from src.index_models import IndexBudget
from src.parser import (
    CodeParser,
    ParseDiagnosticCode,
    ParseStatus,
)
from src.settings import TRUSTED_LOCAL_OWNER


def add_test_project(db: Database, name: str, path: str) -> int:
    return db.add_project(name, path, TRUSTED_LOCAL_OWNER, f"test:{name}")


@pytest.fixture
def db() -> Generator[Database, None, None]:
    database = Database(":memory:")
    yield database
    database.close()


def test_parser_python_reports_indexed_outcome(
    db: Database, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_id = add_test_project(db, "test_project", str(tmp_path))
    path = tmp_path / "test.py"
    path.write_text("class MyClass:\n    def my_func():\n        x = 10\n", encoding="utf-8")

    outcome = CodeParser().parse_file(db, project_id, path)

    assert outcome.status is ParseStatus.INDEXED
    assert outcome.node_count == 3
    assert outcome.diagnostics == ()
    assert capsys.readouterr().out == ""

    node_class = db.get_node_by_name(project_id, "MyClass")
    assert node_class is not None
    assert node_class["type"] == "class"
    assert node_class["start_line"] == 1

    node_func = db.get_node_by_name(project_id, "my_func")
    assert node_func is not None
    assert node_func["type"] == "function"
    assert node_func["start_line"] == 2

    node_var = db.get_node_by_name(project_id, "x")
    assert node_var is not None
    assert node_var["type"] == "variable"
    assert node_var["start_line"] == 3


def test_parser_javascript_reports_indexed_outcome(
    db: Database, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_id = add_test_project(db, "test_project_js", str(tmp_path))
    path = tmp_path / "test.js"
    path.write_text(
        "class MyJSClass {}\nfunction myJSFunc() {}\nconst y = 20;\n",
        encoding="utf-8",
    )

    outcome = CodeParser().parse_file(db, project_id, path)

    assert outcome.status is ParseStatus.INDEXED
    assert outcome.node_count == 3
    assert outcome.diagnostics == ()
    assert capsys.readouterr().out == ""
    node_class = db.get_node_by_name(project_id, "MyJSClass")
    node_function = db.get_node_by_name(project_id, "myJSFunc")
    node_variable = db.get_node_by_name(project_id, "y")
    assert node_class is not None and node_class["type"] == "class"
    assert node_function is not None and node_function["type"] == "function"
    assert node_variable is not None and node_variable["type"] == "variable"


def test_unsupported_file_is_explicitly_skipped_without_stdout(
    db: Database, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_id = add_test_project(db, "unsupported", str(tmp_path))
    path = tmp_path / "notes.txt"
    path.write_text("not supported", encoding="utf-8")

    outcome = CodeParser().parse_file(db, project_id, path)

    assert outcome.status is ParseStatus.SKIPPED
    assert outcome.node_count == 0
    assert [item.code for item in outcome.diagnostics] == [
        ParseDiagnosticCode.UNSUPPORTED_EXTENSION
    ]
    assert capsys.readouterr().out == ""


def test_unreadable_file_is_explicitly_failed_without_stdout(
    db: Database, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_id = add_test_project(db, "unreadable", str(tmp_path))
    missing_path = tmp_path / "missing.py"

    outcome = CodeParser().parse_file(db, project_id, missing_path)

    assert outcome.status is ParseStatus.FAILED
    assert outcome.node_count == 0
    assert [item.code for item in outcome.diagnostics] == [
        ParseDiagnosticCode.FILE_READ_FAILED
    ]
    assert outcome.diagnostics[0].exception_type == "FileNotFoundError"
    assert capsys.readouterr().out == ""


def test_syntax_error_is_explicitly_failed_without_stdout(
    db: Database, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_id = add_test_project(db, "syntax_error", str(tmp_path))
    path = tmp_path / "broken.py"
    path.write_text("def broken(:\n", encoding="utf-8")

    outcome = CodeParser().parse_file(db, project_id, path)

    assert outcome.status is ParseStatus.FAILED
    assert outcome.node_count == 0
    assert [item.code for item in outcome.diagnostics] == [
        ParseDiagnosticCode.SYNTAX_ERROR
    ]
    assert capsys.readouterr().out == ""


def test_query_failure_is_explicit_and_closes_the_file(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_id = add_test_project(db, "query_failure", str(tmp_path))
    parser = CodeParser()
    path = tmp_path / "example.py"
    path.write_bytes(b"value = 1\n")
    real_open = Path.open
    handles: list[BinaryIO] = []

    def tracking_open(
        target: Path, *args: object, **kwargs: object
    ) -> BinaryIO:
        del args, kwargs
        handle = cast(BinaryIO, real_open(target, "rb"))
        handles.append(handle)
        return handle

    class FailingCursor:
        def __init__(self, query: object) -> None:
            del query

        def matches(self, root_node: object) -> object:
            del root_node
            raise RuntimeError("query execution failed")

    monkeypatch.setattr(Path, "open", tracking_open)
    monkeypatch.setattr(parser_module.tree_sitter, "QueryCursor", FailingCursor)

    outcome = parser.parse_file(db, project_id, path)

    assert outcome.status is ParseStatus.FAILED
    assert outcome.node_count == 0
    assert [item.code for item in outcome.diagnostics] == [
        ParseDiagnosticCode.QUERY_EXECUTION_FAILED
    ]
    assert handles and handles[0].closed
    assert capsys.readouterr().out == ""


def test_failed_index_write_should_not_leave_partial_database_state(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = add_test_project(db, "non_atomic_write", str(tmp_path))
    path = tmp_path / "two-symbols.py"
    path.write_text("class One:\n    pass\n\nclass Two:\n    pass\n", encoding="utf-8")
    real_add_node = db.add_node
    call_count = 0

    def fail_second_node(
        target_project_id: int,
        file_id: int,
        node_type: str,
        name: str,
        start_line: int,
        end_line: int,
    ) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("injected second-node write failure")
        return real_add_node(
            target_project_id,
            file_id,
            node_type,
            name,
            start_line,
            end_line,
        )

    monkeypatch.setattr(db, "add_node", fail_second_node)

    outcome = CodeParser().parse_file(db, project_id, path)

    assert outcome.status is ParseStatus.FAILED
    assert outcome.diagnostics[0].code is ParseDiagnosticCode.INDEX_WRITE_FAILED
    file_count = db.conn.execute(
        "SELECT COUNT(*) FROM files WHERE project_id = ?", (project_id,)
    ).fetchone()[0]
    node_count = db.conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE project_id = ?", (project_id,)
    ).fetchone()[0]
    assert (file_count, node_count) == (0, 0)
    assert db.transaction_depth == 0
    assert not db.conn.in_transaction


def test_over_cap_symbol_fails_before_any_database_write(
    db: Database, tmp_path: Path
) -> None:
    project_id = add_test_project(db, "over_cap_symbol", str(tmp_path))
    path = tmp_path / "oversized.py"
    parameters = ", ".join(f"parameter_{index}: int" for index in range(130))
    path.write_text(
        f"def oversized({parameters}) -> int:\n    return 1\n",
        encoding="utf-8",
    )

    outcome = CodeParser().parse_file(db, project_id, path)

    assert outcome.status is ParseStatus.FAILED
    file_count = db.conn.execute(
        "SELECT COUNT(*) FROM files WHERE project_id = ?", (project_id,)
    ).fetchone()[0]
    node_count = db.conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE project_id = ?", (project_id,)
    ).fetchone()[0]
    assert (file_count, node_count) == (0, 0)


def test_file_over_budget_skips_before_parser_and_database(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = add_test_project(db, "over_budget", str(tmp_path))
    path = tmp_path / "large.py"
    path.write_bytes(b"x=10\n")
    parser = CodeParser()

    def forbidden_parse(snapshot: object) -> object:
        raise AssertionError("over-budget source must not reach the parser")

    monkeypatch.setattr(parser, "parse_snapshot", forbidden_parse)

    outcome = parser.parse_file(
        db,
        project_id,
        path,
        budget=IndexBudget(max_file_bytes=4, max_total_bytes=4),
    )

    assert outcome.status is ParseStatus.SKIPPED
    assert outcome.diagnostics[0].code is ParseDiagnosticCode.FILE_TOO_LARGE
    files = db.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    nodes = db.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    assert (files, nodes) == (0, 0)


def test_successful_parser_transaction_is_visible_to_an_observer(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "parser.db"
    db = Database(database_path)
    try:
        project_id = add_test_project(db, "committed", str(tmp_path))
        path = tmp_path / "committed.py"
        path.write_text("class Committed:\n    pass\n", encoding="utf-8")

        outcome = CodeParser().parse_file(db, project_id, path)

        with sqlite3.connect(database_path) as observer:
            file_count = observer.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            node_count = observer.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    finally:
        db.close()

    assert outcome.status is ParseStatus.INDEXED
    assert (file_count, node_count) == (1, 1)
