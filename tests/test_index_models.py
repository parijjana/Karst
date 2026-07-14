from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid5

import pytest

from src import index_models
from src.index_identity import derive_file_stable_id, derive_symbol_stable_id
from src.index_models import (
    DiagnosticSeverity,
    FileCandidate,
    IndexDiagnostic,
    ParsedFile,
    ParsedSymbol,
    ParseStatus,
    SourceSnapshot,
)


PROJECT_ID = str(uuid5(NAMESPACE_URL, "project:/workspace"))
FILE_ID = derive_file_stable_id(PROJECT_ID, "src/example.py")
OTHER_FILE_ID = derive_file_stable_id(PROJECT_ID, "src/other.py")
SYMBOL_ID = derive_symbol_stable_id(
    FILE_ID, "python", "class", "example.Example", None
)
def candidate(**overrides: Any) -> FileCandidate:
    values: dict[str, Any] = {
        "project_stable_id": PROJECT_ID,
        "relative_path": "src/example.py",
        "stable_file_id": FILE_ID,
    }
    values.update(overrides)
    return FileCandidate(**values)


def snapshot(content: bytes = b"class Example:\n    pass\n") -> SourceSnapshot:
    return SourceSnapshot(candidate(), content)


def symbol(**overrides: Any) -> ParsedSymbol:
    values: dict[str, Any] = {
        "stable_symbol_id": SYMBOL_ID,
        "file_stable_id": FILE_ID,
        "language": "python",
        "kind": "class",
        "name": "Example",
        "qualified_name": "example.Example",
        "start_line": 1,
        "end_line": 2,
    }
    values.update(overrides)
    return ParsedSymbol(**values)


def diagnostic(**overrides: Any) -> IndexDiagnostic:
    values: dict[str, Any] = {
        "severity": DiagnosticSeverity.ERROR,
        "code": "syntax_error",
        "message": "The source contains invalid syntax.",
        "relative_path": "src/example.py",
        "exception_type": "SyntaxError",
    }
    values.update(overrides)
    return IndexDiagnostic(**values)


def test_public_contract_is_explicit() -> None:
    assert set(index_models.__all__) == {
        "CancellationSignal",
        "DiagnosticSeverity",
        "FileCandidate",
        "IndexBudget",
        "IndexCounts",
        "IndexDiagnostic",
        "IndexResult",
        "IndexStatus",
        "ParsedFile",
        "ParsedSymbol",
        "ParseStatus",
        "SourceSnapshot",
    }


def test_parsed_file_rejects_symbols_from_another_file() -> None:
    foreign_id = derive_symbol_stable_id(
        OTHER_FILE_ID, "python", "class", "other.Other", None
    )
    foreign = symbol(
        stable_symbol_id=foreign_id,
        file_stable_id=OTHER_FILE_ID,
        name="Other",
        qualified_name="other.Other",
    )

    with pytest.raises(ValueError, match="another file"):
        ParsedFile(snapshot(), ParseStatus.INDEXED, symbols=(foreign,))


@pytest.mark.parametrize(
    "message",
    [
        "Failed:/home/user/file.py",
        "/secret.py",
        r"C:\secret.py",
        r"\\server\share\secret.py",
        r"Failed at C:\Users\developer\repo\file.py",
        "Failed at C:/Users/developer/repo/file.py",
        r"Failed at \\server\share\repo\file.py",
        "Failed at //server/share/repo/file.py",
        "Failed at /home/developer/repo/file.py",
        "Failed at file:///home/developer/repo/file.py",
    ],
)
def test_diagnostic_message_rejects_absolute_path_leakage(message: str) -> None:
    with pytest.raises(ValueError, match="path"):
        diagnostic(message=message)


def test_parse_status_invariants_are_explicit() -> None:
    info = diagnostic(
        severity=DiagnosticSeverity.INFO,
        code="parse_note",
        message="The file used a supported fallback.",
        exception_type=None,
    )
    warning = diagnostic(
        severity=DiagnosticSeverity.WARNING,
        code="partial_parse",
        message="Some optional syntax was ignored.",
        exception_type=None,
    )
    indexed = ParsedFile(
        snapshot(), ParseStatus.INDEXED, symbols=(symbol(),), diagnostics=(info, warning)
    )
    skipped = ParsedFile(
        snapshot(), ParseStatus.SKIPPED, diagnostics=(info, warning)
    )
    failed = ParsedFile(
        snapshot(), ParseStatus.FAILED, diagnostics=(diagnostic(),)
    )

    assert indexed.status is ParseStatus.INDEXED
    assert skipped.symbols == ()
    assert failed.symbols == ()
    with pytest.raises(ValueError, match="failed parse"):
        ParsedFile(
            snapshot(),
            ParseStatus.FAILED,
            symbols=(symbol(),),
            diagnostics=(diagnostic(),),
        )
    with pytest.raises(ValueError, match="error or fatal"):
        ParsedFile(snapshot(), ParseStatus.FAILED, diagnostics=(warning,))
    with pytest.raises(ValueError, match="skipped parse"):
        ParsedFile(snapshot(), ParseStatus.SKIPPED)
    with pytest.raises(ValueError, match="skipped parse"):
        ParsedFile(snapshot(), ParseStatus.SKIPPED, diagnostics=(diagnostic(),))
    with pytest.raises(ValueError, match="indexed parse"):
        ParsedFile(
            snapshot(), ParseStatus.INDEXED, diagnostics=(diagnostic(),)
        )
