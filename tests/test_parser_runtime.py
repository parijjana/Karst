from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
import tree_sitter

from src import parser as parser_module
from src.database import Database
from src.parser import CodeParser, ParseDiagnosticCode, ParseStatus, ParseSummary
from src.settings import TRUSTED_LOCAL_OWNER


@pytest.fixture
def db() -> Generator[Database, None, None]:
    database = Database(":memory:")
    yield database
    database.close()


def add_project(db: Database, name: str, path: Path) -> int:
    return db.add_project(name, str(path), TRUSTED_LOCAL_OWNER, f"test:{name}")


def test_query_compilation_failure_is_reported_without_stdout(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_id = add_project(db, "compile_failure", tmp_path)
    path = tmp_path / "broken-grammar.py"
    path.write_text("value = 1\n", encoding="utf-8")
    real_query = parser_module.tree_sitter.Query

    def fail_python_query(
        language: tree_sitter.Language, source: str
    ) -> tree_sitter.Query:
        if "(assignment left:" in source:
            raise RuntimeError("grammar query did not compile")
        return real_query(language, source)

    monkeypatch.setattr(parser_module.tree_sitter, "Query", fail_python_query)

    parser = CodeParser()
    outcome = parser.parse_file(db, project_id, path)
    diagnostics = tuple(
        item for item in parser.initialization_diagnostics if item.extension == ".py"
    )

    assert [item.code for item in diagnostics] == [
        ParseDiagnosticCode.QUERY_COMPILATION_FAILED
    ]
    assert outcome.status is ParseStatus.FAILED
    assert outcome.diagnostics[0].code is ParseDiagnosticCode.QUERY_COMPILATION_FAILED
    assert outcome.diagnostics[0].file_path == path
    assert diagnostics[0].file_path is None
    assert "grammar query did not compile" in caplog.text
    assert capsys.readouterr().out == ""


def test_language_readiness_distinguishes_dart_module_from_usable_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_query = parser_module.tree_sitter.Query

    def fail_dart_query(
        language: tree_sitter.Language, source: str
    ) -> tree_sitter.Query:
        if "function_signature" in source:
            raise RuntimeError("Dart query is incompatible with its grammar")
        return real_query(language, source)

    monkeypatch.setattr(parser_module.tree_sitter, "Query", fail_dart_query)
    parser = CodeParser()
    readiness = parser.language_readiness[".dart"]

    assert tuple(parser.language_readiness) == tuple(sorted(parser.language_readiness))
    assert parser_module.DART_AVAILABLE is parser_module.DART_MODULE_AVAILABLE
    assert readiness.module_available is parser_module.DART_MODULE_AVAILABLE
    assert not readiness.ready
    assert ".dart" not in parser.supported_extensions
    assert readiness.diagnostic is not None
    expected = (
        ParseDiagnosticCode.QUERY_COMPILATION_FAILED
        if parser_module.DART_MODULE_AVAILABLE
        else ParseDiagnosticCode.GRAMMAR_MODULE_UNAVAILABLE
    )
    assert readiness.diagnostic.code is expected
    assert readiness.parser_ready is parser_module.DART_MODULE_AVAILABLE
    assert not readiness.query_ready


def test_installed_dart_grammar_and_symbol_query_are_ready() -> None:
    parser = CodeParser()
    readiness = parser.language_readiness[".dart"]

    assert parser_module.DART_MODULE_AVAILABLE
    assert readiness.module_available
    assert readiness.parser_ready
    assert readiness.query_ready
    assert readiness.diagnostic is None
    assert ".dart" in parser.supported_extensions


def test_parse_summary_counts_indexed_skipped_and_failed_outcomes(
    db: Database, tmp_path: Path
) -> None:
    project_id = add_project(db, "summary", tmp_path)
    supported = tmp_path / "supported.py"
    supported.write_text("value = 1\n", encoding="utf-8")
    unsupported = tmp_path / "unsupported.txt"
    unsupported.write_text("value", encoding="utf-8")
    parser = CodeParser()
    outcomes = (
        parser.parse_file(db, project_id, supported),
        parser.parse_file(db, project_id, unsupported),
        parser.parse_file(db, project_id, tmp_path / "missing.py"),
    )
    summary = ParseSummary(outcomes)

    assert summary.total_count == 3
    assert summary.indexed_count == 1
    assert summary.skipped_count == 1
    assert summary.failed_count == 1
    assert summary.indexed_outcomes == (outcomes[0],)
    assert summary.skipped_outcomes == (outcomes[1],)
    assert summary.failed_outcomes == (outcomes[2],)
