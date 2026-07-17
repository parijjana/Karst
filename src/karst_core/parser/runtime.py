from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

import tree_sitter
import tree_sitter_javascript
import tree_sitter_markdown
import tree_sitter_python
import tree_sitter_typescript

from src.karst_core.parser.models import (
    LanguageReadiness,
    ParseDiagnostic,
    ParseDiagnosticCode,
)

try:
    import tree_sitter_dart

    DART_MODULE_AVAILABLE = True
except ImportError:
    DART_MODULE_AVAILABLE = False

DART_AVAILABLE = DART_MODULE_AVAILABLE
logger = logging.getLogger(__name__)


SYMBOL_QUERIES = {
    ".py": """
        (class_definition name: (identifier) @class.name) @class.def
        (function_definition name: (identifier) @function.name) @function.def
        (assignment left: (identifier) @variable.name) @variable.def
    """,
    ".js": """
        (class_declaration name: (identifier) @class.name) @class.def
        (function_declaration name: (identifier) @function.name) @function.def
        (method_definition
          name: (property_identifier) @method.name) @method.def
        (lexical_declaration
          (variable_declarator name: (identifier) @variable.name) @variable.def)
        (variable_declaration
          (variable_declarator name: (identifier) @variable.name) @variable.def)
    """,
    ".ts": """
        (class_declaration name: (type_identifier) @class.name) @class.def
        (function_declaration name: (identifier) @function.name) @function.def
        (function_signature name: (identifier) @function.name) @function.def
        (method_definition
          name: (property_identifier) @method.name) @method.def
        (method_signature
          name: (property_identifier) @method.name) @method.def
        (lexical_declaration
          (variable_declarator name: (identifier) @variable.name) @variable.def)
        (variable_declaration
          (variable_declarator name: (identifier) @variable.name) @variable.def)
    """,
    ".dart": """
        (class_definition name: (identifier) @class.name) @class.def
        (program
          (function_signature
            name: (identifier) @function.name) @function.def)
        (method_signature
          (function_signature
            name: (identifier) @method.name) @method.def)
        [
          (constructor_signature)
          (constant_constructor_signature)
          (factory_constructor_signature)
          (redirecting_factory_constructor_signature)
        ] @constructor.def
    """,
    ".md": """
        (atx_heading heading_content: (inline) @heading.name) @heading.def
        (setext_heading
          heading_content: (paragraph (inline) @heading.name)) @heading.def
    """,
}

LANGUAGE_NAMES = {
    ".dart": "dart",
    ".js": "javascript",
    ".md": "markdown",
    ".py": "python",
    ".ts": "typescript",
}


@dataclass(frozen=True)
class ParserRuntime:
    languages: dict[str, tree_sitter.Language]
    parsers: dict[str, tree_sitter.Parser]
    queries: dict[str, tree_sitter.Query]
    query_diagnostics: dict[str, ParseDiagnostic]
    language_readiness: Mapping[str, LanguageReadiness]
    supported_extensions: frozenset[str]
    initialization_diagnostics: tuple[ParseDiagnostic, ...]


def _language_factories():
    factories = {
        ".js": lambda: tree_sitter.Language(tree_sitter_javascript.language()),
        ".md": lambda: tree_sitter.Language(tree_sitter_markdown.language()),
        ".py": lambda: tree_sitter.Language(tree_sitter_python.language()),
        ".ts": lambda: tree_sitter.Language(
            tree_sitter_typescript.language_typescript()
        ),
    }
    if DART_MODULE_AVAILABLE:
        factories[".dart"] = lambda: tree_sitter.Language(tree_sitter_dart.language())
    return factories


def _diagnostic(
    code: ParseDiagnosticCode,
    message: str,
    extension: str,
    error: BaseException | None = None,
) -> ParseDiagnostic:
    return ParseDiagnostic(
        code=code,
        message=message,
        extension=extension,
        exception_type=type(error).__name__ if error is not None else None,
    )


def initialize_parser_runtime(queries: Mapping[str, str]) -> ParserRuntime:
    languages: dict[str, tree_sitter.Language] = {}
    parsers: dict[str, tree_sitter.Parser] = {}
    compiled_queries: dict[str, tree_sitter.Query] = {}
    diagnostics: dict[str, ParseDiagnostic] = {}
    readiness: dict[str, LanguageReadiness] = {}

    if not DART_MODULE_AVAILABLE:
        dart_diagnostic = _diagnostic(
            ParseDiagnosticCode.GRAMMAR_MODULE_UNAVAILABLE,
            "The optional Dart grammar module is not installed",
            ".dart",
        )
        diagnostics[".dart"] = dart_diagnostic
        readiness[".dart"] = LanguageReadiness(
            ".dart", False, False, False, dart_diagnostic
        )

    for extension, factory in sorted(_language_factories().items()):
        try:
            language = factory()
            parser = tree_sitter.Parser(language)
        except Exception as error:
            item = _diagnostic(
                ParseDiagnosticCode.GRAMMAR_INITIALIZATION_FAILED,
                f"Could not initialize the {extension} parser: {error}",
                extension,
                error,
            )
            diagnostics[extension] = item
            readiness[extension] = LanguageReadiness(
                extension, True, False, False, item
            )
            logger.error(item.message, exc_info=True)
            continue

        languages[extension] = language
        parsers[extension] = parser
        query_source = queries.get(extension)
        if query_source is None:
            item = _diagnostic(
                ParseDiagnosticCode.QUERY_COMPILATION_FAILED,
                f"No symbol query is configured for {extension}",
                extension,
            )
        else:
            try:
                compiled_queries[extension] = tree_sitter.Query(language, query_source)
            except Exception as error:
                item = _diagnostic(
                    ParseDiagnosticCode.QUERY_COMPILATION_FAILED,
                    f"Could not compile the {extension} symbol query: {error}",
                    extension,
                    error,
                )
            else:
                readiness[extension] = LanguageReadiness(extension, True, True, True)
                continue

        diagnostics[extension] = item
        readiness[extension] = LanguageReadiness(extension, True, True, False, item)
        logger.error(item.message, exc_info=item.exception_type is not None)

    ordered_readiness = MappingProxyType(dict(sorted(readiness.items())))
    supported = frozenset(
        extension for extension, state in ordered_readiness.items() if state.ready
    )
    initialization_diagnostics = tuple(
        state.diagnostic
        for state in ordered_readiness.values()
        if state.diagnostic is not None
    )
    return ParserRuntime(
        languages,
        parsers,
        compiled_queries,
        diagnostics,
        ordered_readiness,
        supported,
        initialization_diagnostics,
    )
