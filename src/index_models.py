"""Compatibility facade for core indexing models."""

from src.karst_core.indexing.models import (
    CancellationSignal as CancellationSignal,
    DiagnosticSeverity as DiagnosticSeverity,
    FileCandidate as FileCandidate,
    IndexBudget as IndexBudget,
    IndexCounts as IndexCounts,
    IndexDiagnostic as IndexDiagnostic,
    IndexResult as IndexResult,
    IndexStatus as IndexStatus,
    ParsedFile as ParsedFile,
    ParsedSymbol as ParsedSymbol,
    ParseStatus as ParseStatus,
    SourceSnapshot as SourceSnapshot,
)
from src.karst_core.indexing.models import __all__ as __all__
