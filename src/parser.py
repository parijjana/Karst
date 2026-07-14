from __future__ import annotations

import logging
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import tree_sitter as tree_sitter

from src.database import Database
from src.index_models import (
    DiagnosticSeverity,
    IndexBudget,
    IndexDiagnostic,
    ParsedFile,
    SourceSnapshot,
)
from src.parser_models import (
    LanguageReadiness,
    ParseDiagnostic,
    ParseDiagnosticCode,
    ParseOutcome,
    ParserError,
    ParseStatus,
    ParseSummary,
)
from src.parser_runtime import (
    DART_AVAILABLE,
    DART_MODULE_AVAILABLE,
    SYMBOL_QUERIES,
    initialize_parser_runtime,
)
from src.parser_snapshot import read_snapshot
from src.parser_symbols import parse_snapshot


logger = logging.getLogger(__name__)
DEFAULT_PARSE_BUDGET = IndexBudget(
    max_file_bytes=8 * 1024 * 1024,
    max_total_bytes=8 * 1024 * 1024,
)
__all__ = [
    "CodeParser",
    "DART_AVAILABLE",
    "DART_MODULE_AVAILABLE",
    "LanguageReadiness",
    "ParseDiagnostic",
    "ParseDiagnosticCode",
    "ParseOutcome",
    "ParserError",
    "ParseStatus",
    "ParseSummary",
    "SourceSnapshot",
    "parse_snapshot",
    "read_snapshot",
]


def _canonical_project_id(value: object) -> str:
    raw = str(value)
    try:
        parsed = UUID(raw)
    except ValueError:
        return str(uuid5(NAMESPACE_URL, f"karst-project:{raw}"))
    if parsed.version == 5 and str(parsed) == raw:
        return raw
    return str(uuid5(NAMESPACE_URL, f"karst-project:{raw}"))


def _relative_path(path: Path, root: Path) -> str:
    if not path.is_absolute():
        candidate = path.as_posix()
    else:
        try:
            candidate = path.resolve(strict=False).relative_to(
                root.resolve(strict=False)
            ).as_posix()
        except ValueError:
            candidate = path.name
    return candidate or path.name


def _compatibility_diagnostic(
    path: Path, item: IndexDiagnostic
) -> ParseDiagnostic:
    try:
        code = ParseDiagnosticCode(item.code)
    except ValueError:
        code = ParseDiagnosticCode.PARSE_FAILED
    return ParseDiagnostic(
        code=code,
        message=item.message,
        file_path=path,
        extension=path.suffix.lower(),
        exception_type=item.exception_type,
    )


def _diagnostic_outcome(path: Path, item: IndexDiagnostic) -> ParseOutcome:
    status = ParseStatus.FAILED if item.severity.is_terminal else ParseStatus.SKIPPED
    return ParseOutcome(
        path,
        status,
        diagnostics=(_compatibility_diagnostic(path, item),),
    )


class CodeParser:
    """Compatibility adapter around pure snapshot parsing and atomic storage."""

    def __init__(self) -> None:
        self.queries_str = dict(SYMBOL_QUERIES)
        self.runtime = initialize_parser_runtime(self.queries_str)
        self.languages = self.runtime.languages
        self.parsers = self.runtime.parsers
        self.queries = self.runtime.queries
        self._query_diagnostics = self.runtime.query_diagnostics
        self.language_readiness = self.runtime.language_readiness
        self.supported_extensions = self.runtime.supported_extensions
        self.initialization_diagnostics = self.runtime.initialization_diagnostics

    def parse_snapshot(self, snapshot: SourceSnapshot) -> ParsedFile:
        return parse_snapshot(snapshot, self.runtime)

    def _snapshot_context(
        self, db: Database, project_id: int, path: Path
    ) -> tuple[str, str]:
        row = db.conn.execute(
            "SELECT stable_id, path FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Project does not exist.")
        return _canonical_project_id(row[0]), _relative_path(path, Path(str(row[1])))

    def parse_file(
        self,
        db: Database,
        project_id: int,
        file_path: str | Path,
        *,
        budget: IndexBudget | None = None,
    ) -> ParseOutcome:
        path = Path(file_path)
        try:
            project_stable_id, relative_path = self._snapshot_context(
                db, project_id, path
            )
        except Exception as error:
            logger.error("Could not resolve parser project context.", exc_info=True)
            diagnostic = IndexDiagnostic(
                DiagnosticSeverity.ERROR,
                "index_write_failed",
                "Parser project context could not be resolved.",
                exception_type=type(error).__name__,
            )
            return _diagnostic_outcome(path, diagnostic)

        snapshot = read_snapshot(
            path,
            project_stable_id,
            relative_path,
            budget or DEFAULT_PARSE_BUDGET,
        )
        if isinstance(snapshot, IndexDiagnostic):
            return _diagnostic_outcome(path, snapshot)
        parsed = self.parse_snapshot(snapshot)
        compatibility_diagnostics = tuple(
            _compatibility_diagnostic(path, item) for item in parsed.diagnostics
        )
        if parsed.status.value != ParseStatus.INDEXED.value:
            return ParseOutcome(
                path,
                ParseStatus(parsed.status.value),
                diagnostics=compatibility_diagnostics,
            )

        try:
            with db.transaction():
                file_id = db.add_file(
                    project_id,
                    str(path),
                    snapshot.content_sha256,
                )
                for symbol in parsed.symbols:
                    legacy_kind = "function" if symbol.kind == "method" else symbol.kind
                    db.add_node(
                        project_id,
                        file_id,
                        legacy_kind,
                        symbol.name,
                        symbol.start_line,
                        symbol.end_line,
                    )
        except Exception as error:
            logger.error("Could not store parsed symbols.", exc_info=True)
            diagnostic = IndexDiagnostic(
                DiagnosticSeverity.ERROR,
                "index_write_failed",
                "Parsed symbols could not be stored.",
                relative_path=relative_path,
                exception_type=type(error).__name__,
            )
            return _diagnostic_outcome(path, diagnostic)
        return ParseOutcome(
            path,
            ParseStatus.INDEXED,
            node_count=len(parsed.symbols),
            file_id=file_id,
            diagnostics=compatibility_diagnostics,
        )
