from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from src.karst_core.indexing.identity import (
    FileCandidate,
    ParsedSymbol,
    SourceSnapshot,
    _require_relative_posix_path,
)


__all__ = [
    "CancellationSignal", "DiagnosticSeverity", "FileCandidate", "IndexBudget",
    "IndexCounts", "IndexDiagnostic", "IndexResult", "IndexStatus", "ParsedFile",
    "ParsedSymbol", "ParseStatus", "SourceSnapshot",
]


_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_DRIVE_PREFIX_PATTERN = re.compile(r"(?i)(?:^|[^a-z0-9_])[a-z]:")


def _nonnegative(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer.")
    return value


def _positive(value: object, field: str) -> int:
    parsed = _nonnegative(value, field)
    if parsed == 0:
        raise ValueError(f"{field} must be a positive integer.")
    return parsed


def _bounded(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} must be nonempty and at most {maximum} characters.")
    return value


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest.")
    return value


class DiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    FATAL = "fatal"

    @property
    def is_terminal(self) -> bool:
        return self in {DiagnosticSeverity.ERROR, DiagnosticSeverity.FATAL}


class ParseStatus(str, Enum):
    INDEXED = "indexed"
    SKIPPED = "skipped"
    FAILED = "failed"


class IndexStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self is not IndexStatus.IN_PROGRESS


@runtime_checkable
class CancellationSignal(Protocol):
    def is_cancelled(self) -> bool:
        """Return whether cooperative indexing work should stop."""


@dataclass(frozen=True, slots=True)
class IndexBudget:
    max_files: int = 0
    max_file_bytes: int = 0
    max_total_bytes: int = 0
    max_depth: int = 0
    max_diagnostics: int = 1
    max_symbols: int = 0
    max_edges: int = 0
    max_update_files: int = 0
    max_duration_ms: int = 0

    def __post_init__(self) -> None:
        names = (
            "max_files", "max_file_bytes", "max_total_bytes", "max_depth",
            "max_diagnostics", "max_symbols", "max_edges", "max_update_files",
            "max_duration_ms",
        )
        for name in names:
            _nonnegative(getattr(self, name), name)
        if self.max_diagnostics < 1:
            raise ValueError("max_diagnostics must be at least 1.")
        if self.max_file_bytes > self.max_total_bytes:
            raise ValueError("Per-file byte budget cannot exceed total byte budget.")


@dataclass(frozen=True, slots=True)
class IndexDiagnostic:
    severity: DiagnosticSeverity
    code: str
    message: str
    relative_path: str | None = None
    exception_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.severity, DiagnosticSeverity):
            raise ValueError("severity must be a DiagnosticSeverity.")
        if not isinstance(self.code, str) or _CODE_PATTERN.fullmatch(self.code) is None:
            raise ValueError("Diagnostic code must be canonical lower snake case.")
        message = _bounded(self.message, "diagnostic message", 1024)
        if (
            "/" in message
            or "\\" in message
            or "file:" in message.casefold()
            or _DRIVE_PREFIX_PATTERN.search(message) is not None
        ):
            raise ValueError("Diagnostic message must not contain path data.")
        if self.relative_path is not None:
            _require_relative_posix_path(self.relative_path)
        if self.exception_type is not None:
            _bounded(self.exception_type, "exception_type", 128)


@dataclass(frozen=True, slots=True)
class ParsedFile:
    snapshot: SourceSnapshot
    status: ParseStatus
    symbols: tuple[ParsedSymbol, ...] = ()
    diagnostics: tuple[IndexDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, SourceSnapshot):
            raise ValueError("snapshot must be a SourceSnapshot.")
        if not isinstance(self.status, ParseStatus):
            raise ValueError("status must be a ParseStatus.")
        symbols, diagnostics = tuple(self.symbols), tuple(self.diagnostics)
        if any(not isinstance(item, ParsedSymbol) for item in symbols):
            raise ValueError("symbols must contain ParsedSymbol values.")
        if any(not isinstance(item, IndexDiagnostic) for item in diagnostics):
            raise ValueError("diagnostics must contain IndexDiagnostic values.")
        file_id = self.snapshot.candidate.stable_file_id
        if any(item.file_stable_id != file_id for item in symbols):
            raise ValueError("Parsed file contains a symbol from another file.")
        ids = tuple(item.stable_symbol_id for item in symbols)
        if len(ids) != len(set(ids)):
            raise ValueError("Parsed file contains a duplicate stable symbol ID.")
        path = self.snapshot.candidate.relative_path
        if any(item.relative_path not in {None, path} for item in diagnostics):
            raise ValueError("Parsed-file diagnostic path does not match its snapshot.")
        terminal = any(item.severity.is_terminal for item in diagnostics)
        if self.status is ParseStatus.FAILED:
            if symbols:
                raise ValueError("failed parse cannot contain symbols.")
            if not terminal:
                raise ValueError("failed parse requires an error or fatal diagnostic.")
        elif self.status is ParseStatus.SKIPPED:
            if symbols or not diagnostics:
                raise ValueError("skipped parse requires no symbols and a reason.")
            if terminal:
                raise ValueError(
                    "skipped parse may contain only info or warning diagnostics."
                )
        elif terminal:
            raise ValueError(
                "indexed parse may contain only info or warning diagnostics."
            )
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "diagnostics", diagnostics)


@dataclass(frozen=True, slots=True)
class IndexCounts:
    discovered_files: int = 0
    indexed_files: int = 0
    unchanged_files: int = 0
    skipped_files: int = 0
    deleted_files: int = 0
    renamed_files: int = 0
    failed_files: int = 0
    symbol_count: int = 0
    edge_count: int = 0
    diagnostic_count: int = 0

    def __post_init__(self) -> None:
        for name in (
            "discovered_files", "indexed_files", "unchanged_files", "skipped_files",
            "deleted_files", "renamed_files", "failed_files", "symbol_count",
            "edge_count", "diagnostic_count",
        ):
            _nonnegative(getattr(self, name), name)
        if self.processed_files > self.discovered_files:
            raise ValueError("processed files cannot exceed discovered files.")

    @property
    def processed_files(self) -> int:
        return (
            self.indexed_files + self.unchanged_files + self.skipped_files
            + self.renamed_files + self.failed_files
        )


@dataclass(frozen=True, slots=True)
class IndexResult:
    status: IndexStatus
    counts: IndexCounts = IndexCounts()
    diagnostics: tuple[IndexDiagnostic, ...] = ()
    generation_id: int | None = None
    manifest_sha256: str | None = None
    promoted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.status, IndexStatus) or not isinstance(
            self.counts, IndexCounts
        ):
            raise ValueError("status and counts use index contract types.")
        diagnostics = tuple(self.diagnostics)
        if any(not isinstance(item, IndexDiagnostic) for item in diagnostics):
            raise ValueError("diagnostics must contain IndexDiagnostic values.")
        if self.counts.diagnostic_count != len(diagnostics):
            raise ValueError("diagnostic count does not match diagnostics.")
        if self.generation_id is not None:
            _positive(self.generation_id, "generation_id")
        if self.manifest_sha256 is not None:
            _sha256(self.manifest_sha256, "manifest_sha256")
        if not isinstance(self.promoted, bool):
            raise ValueError("promoted must be a boolean.")
        if self.promoted and self.status is not IndexStatus.COMPLETED:
            raise ValueError("only completed results may be promoted.")
        terminal = any(item.severity.is_terminal for item in diagnostics)
        if self.status is IndexStatus.REJECTED:
            if self.generation_id is not None or self.manifest_sha256 is not None:
                raise ValueError("rejected result is pre-staging and has no generation.")
            workload_counts = (
                self.counts.discovered_files,
                self.counts.indexed_files,
                self.counts.unchanged_files,
                self.counts.skipped_files,
                self.counts.deleted_files,
                self.counts.renamed_files,
                self.counts.failed_files,
                self.counts.symbol_count,
                self.counts.edge_count,
            )
            if any(workload_counts):
                raise ValueError("rejected result requires zero workload counts.")
            if not terminal:
                raise ValueError("rejected result requires a terminal diagnostic.")
        elif self.status is IndexStatus.IN_PROGRESS:
            if self.generation_id is None:
                raise ValueError("in-progress result requires a generation.")
            if terminal:
                raise ValueError("in-progress result cannot have a terminal diagnostic.")
        elif self.status is IndexStatus.COMPLETED:
            if self.generation_id is None or self.manifest_sha256 is None:
                raise ValueError("completed result requires generation and manifest.")
            if not self.promoted:
                raise ValueError("completed result must be promoted.")
            if self.counts.failed_files or terminal:
                raise ValueError("completed result cannot contain failures.")
            if self.counts.processed_files != self.counts.discovered_files:
                raise ValueError("completed result must account for every discovered file.")
        elif self.status is IndexStatus.FAILED:
            if self.generation_id is None:
                raise ValueError("failed result requires a generation.")
            if not terminal:
                raise ValueError("failed result requires an error or fatal diagnostic.")
        else:
            if self.generation_id is None:
                raise ValueError("cancelled result requires a generation.")
            if not diagnostics:
                raise ValueError("cancelled result requires a diagnostic.")
        object.__setattr__(self, "diagnostics", diagnostics)

    @property
    def succeeded(self) -> bool:
        return self.status is IndexStatus.COMPLETED
