from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import BinaryIO

from src.karst_core.indexing.identity import derive_file_stable_id
from src.karst_core.indexing.models import (
    DiagnosticSeverity,
    FileCandidate,
    IndexBudget,
    IndexDiagnostic,
    SourceSnapshot,
)


_READ_CHUNK_BYTES = 64 * 1024


def _diagnostic(
    severity: DiagnosticSeverity,
    code: str,
    message: str,
    relative_path: str | None,
    error: BaseException | None = None,
) -> IndexDiagnostic:
    return IndexDiagnostic(
        severity=severity,
        code=code,
        message=message,
        relative_path=relative_path,
        exception_type=type(error).__name__ if error is not None else None,
    )


def _fingerprint(value: object) -> tuple[int, ...]:
    fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    return tuple(int(getattr(value, field, 0)) for field in fields)


def _read_bounded(handle: BinaryIO, maximum: int) -> bytes:
    chunks: list[bytes] = []
    consumed = 0
    ceiling = maximum + 1
    while consumed < ceiling:
        requested = min(_READ_CHUNK_BYTES, ceiling - consumed)
        chunk = handle.read(requested)
        if not isinstance(chunk, bytes):
            raise TypeError("Binary source reads must return bytes.")
        if len(chunk) > requested:
            raise ValueError("Binary source read exceeded the requested bound.")
        if not chunk:
            break
        chunks.append(chunk)
        consumed += len(chunk)
    return b"".join(chunks)


def read_snapshot(
    path: str | Path,
    project_stable_id: str,
    relative_path: str,
    budget: IndexBudget,
) -> SourceSnapshot | IndexDiagnostic:
    """Read one immutable, bounded source snapshot from a single open handle."""
    if not isinstance(budget, IndexBudget):
        raise ValueError("budget must be an IndexBudget.")
    try:
        candidate = FileCandidate(
            project_stable_id=project_stable_id,
            relative_path=relative_path,
            stable_file_id=derive_file_stable_id(project_stable_id, relative_path),
        )
    except ValueError as error:
        return _diagnostic(
            DiagnosticSeverity.ERROR,
            "invalid_source_identity",
            "Source identity violates its text contract.",
            None,
            error,
        )
    source_path = Path(path)
    try:
        with source_path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                return _diagnostic(
                    DiagnosticSeverity.ERROR,
                    "file_read_failed",
                    "Source is not a regular file.",
                    relative_path,
                )
            content = _read_bounded(handle, budget.max_file_bytes)
            after = os.fstat(handle.fileno())
    except (OSError, TypeError, ValueError) as error:
        return _diagnostic(
            DiagnosticSeverity.ERROR,
            "file_read_failed",
            "Source could not be read.",
            relative_path,
            error,
        )

    if _fingerprint(before) != _fingerprint(after):
        return _diagnostic(
            DiagnosticSeverity.ERROR,
            "source_changed_during_read",
            "Source changed while its snapshot was being read.",
            relative_path,
        )
    if len(content) > budget.max_file_bytes:
        return _diagnostic(
            DiagnosticSeverity.WARNING,
            "file_too_large",
            "Source exceeds the per-file byte budget.",
            relative_path,
        )
    return SourceSnapshot(candidate, content)
