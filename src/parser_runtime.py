"""Compatibility facade for core parser runtime support."""

import tree_sitter as tree_sitter

from src.karst_core.parser.runtime import (
    DART_AVAILABLE as DART_AVAILABLE,
    DART_MODULE_AVAILABLE as DART_MODULE_AVAILABLE,
    LANGUAGE_NAMES as LANGUAGE_NAMES,
    SYMBOL_QUERIES as SYMBOL_QUERIES,
    ParserRuntime as ParserRuntime,
    initialize_parser_runtime as initialize_parser_runtime,
)

__all__ = (
    "DART_AVAILABLE",
    "DART_MODULE_AVAILABLE",
    "LANGUAGE_NAMES",
    "SYMBOL_QUERIES",
    "ParserRuntime",
    "initialize_parser_runtime",
)
