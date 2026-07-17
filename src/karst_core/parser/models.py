"""Parser compatibility and result models owned by the Karst data core."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ParserError(Exception):
    """Base exception retained for parser API compatibility."""


class ParseStatus(str, Enum):
    """Final disposition of one requested file."""

    INDEXED = "indexed"
    SKIPPED = "skipped"
    FAILED = "failed"


class ParseDiagnosticCode(str, Enum):
    """Machine-readable reason attached to a parse outcome."""

    UNSUPPORTED_EXTENSION = "unsupported_extension"
    GRAMMAR_MODULE_UNAVAILABLE = "grammar_module_unavailable"
    GRAMMAR_INITIALIZATION_FAILED = "grammar_initialization_failed"
    FILE_READ_FAILED = "file_read_failed"
    FILE_TOO_LARGE = "file_too_large"
    INVALID_SOURCE_IDENTITY = "invalid_source_identity"
    SOURCE_CHANGED_DURING_READ = "source_changed_during_read"
    QUERY_COMPILATION_FAILED = "query_compilation_failed"
    PARSE_FAILED = "parse_failed"
    SYNTAX_ERROR = "syntax_error"
    QUERY_EXECUTION_FAILED = "query_execution_failed"
    SYMBOL_EXTRACTION_FAILED = "symbol_extraction_failed"
    INDEX_WRITE_FAILED = "index_write_failed"


@dataclass(frozen=True)
class ParseDiagnostic:
    """Structured context for a skipped or failed parse request."""

    code: ParseDiagnosticCode
    message: str
    file_path: Path | None = None
    extension: str | None = None
    exception_type: str | None = None


@dataclass(frozen=True)
class LanguageReadiness:
    """Immutable parser/query readiness for one known file extension."""

    extension: str
    module_available: bool
    parser_ready: bool
    query_ready: bool
    diagnostic: ParseDiagnostic | None = None

    @property
    def ready(self) -> bool:
        return self.module_available and self.parser_ready and self.query_ready


@dataclass(frozen=True)
class ParseOutcome:
    """Typed result for one file passed to ``CodeParser.parse_file``."""

    file_path: Path
    status: ParseStatus
    node_count: int = 0
    file_id: int | None = None
    diagnostics: tuple[ParseDiagnostic, ...] = ()


@dataclass(frozen=True)
class ParseSummary:
    """Deterministic aggregate of file-level parse outcomes."""

    outcomes: tuple[ParseOutcome, ...]

    @property
    def total_count(self) -> int:
        return len(self.outcomes)

    def _with_status(self, status: ParseStatus) -> tuple[ParseOutcome, ...]:
        return tuple(item for item in self.outcomes if item.status is status)

    @property
    def indexed_outcomes(self) -> tuple[ParseOutcome, ...]:
        return self._with_status(ParseStatus.INDEXED)

    @property
    def skipped_outcomes(self) -> tuple[ParseOutcome, ...]:
        return self._with_status(ParseStatus.SKIPPED)

    @property
    def failed_outcomes(self) -> tuple[ParseOutcome, ...]:
        return self._with_status(ParseStatus.FAILED)

    @property
    def indexed_count(self) -> int:
        return len(self.indexed_outcomes)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped_outcomes)

    @property
    def failed_count(self) -> int:
        return len(self.failed_outcomes)


@dataclass(frozen=True)
class ParsedSymbol:
    node_type: str
    name: str
    start_line: int
    end_line: int
