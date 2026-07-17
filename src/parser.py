"""Compatibility facade for :mod:`src.karst_core.parser`."""

import tree_sitter as tree_sitter

from src.karst_core.parser.facade import (
    CodeParser as CodeParser,
    DART_AVAILABLE as DART_AVAILABLE,
    DART_MODULE_AVAILABLE as DART_MODULE_AVAILABLE,
    LanguageReadiness as LanguageReadiness,
    ParseDiagnostic as ParseDiagnostic,
    ParseDiagnosticCode as ParseDiagnosticCode,
    ParseOutcome as ParseOutcome,
    ParserError as ParserError,
    ParseStatus as ParseStatus,
    ParseSummary as ParseSummary,
    SourceSnapshot as SourceSnapshot,
    parse_snapshot as parse_snapshot,
    read_snapshot as read_snapshot,
)
from src.karst_core.parser.facade import __all__ as __all__
