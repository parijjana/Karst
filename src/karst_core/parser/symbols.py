from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any

import tree_sitter

from src.karst_core.indexing.identity import derive_symbol_stable_id
from src.karst_core.indexing.models import (
    DiagnosticSeverity,
    IndexDiagnostic,
    ParsedFile,
    ParsedSymbol,
    ParseStatus,
    SourceSnapshot,
)
from src.karst_core.parser.runtime import (
    LANGUAGE_NAMES,
    SYMBOL_QUERIES,
    ParserRuntime,
    initialize_parser_runtime,
)


_CAPTURE_KINDS = (
    "class",
    "constructor",
    "function",
    "method",
    "variable",
    "heading",
)
_CLASS_SCOPES = {
    "abstract_class_declaration",
    "class_declaration",
    "class_definition",
    "extension_declaration",
    "interface_declaration",
    "mixin_declaration",
}
_CALLABLE_SCOPES = {
    "constant_constructor_signature",
    "constructor_signature",
    "factory_constructor_signature",
    "function_declaration",
    "function_definition",
    "function_signature",
    "method_definition",
    "method_signature",
    "redirecting_factory_constructor_signature",
}
_NAMED_SCOPES = _CLASS_SCOPES | _CALLABLE_SCOPES | {"internal_module"}
_CALLABLE_KINDS = {"constructor", "function", "method"}


@dataclass(frozen=True, slots=True)
class _Draft:
    kind: str
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    start_byte: int
    signature: str | None


def _diagnostic(
    severity: DiagnosticSeverity,
    code: str,
    message: str,
    snapshot: SourceSnapshot,
    exception_type: str | None = None,
) -> IndexDiagnostic:
    return IndexDiagnostic(
        severity=severity,
        code=code,
        message=message,
        relative_path=snapshot.candidate.relative_path,
        exception_type=exception_type,
    )


def _text(content: bytes, node: tree_sitter.Node) -> str:
    return content[node.start_byte : node.end_byte].decode("utf-8")


def _scope_nodes(definition: tree_sitter.Node) -> list[tree_sitter.Node]:
    scopes: list[tree_sitter.Node] = []
    current = definition.parent
    while current is not None:
        if current.type in _NAMED_SCOPES:
            scopes.append(current)
        current = current.parent
    scopes.reverse()
    return scopes


def _scope_name(content: bytes, node: tree_sitter.Node) -> str | None:
    name_node = node.child_by_field_name("name")
    return _text(content, name_node) if name_node is not None else None


def _kind(label: str, definition: tree_sitter.Node) -> str:
    if label != "function":
        return label
    scopes = _scope_nodes(definition)
    if scopes and scopes[-1].type in _CLASS_SCOPES:
        return "method"
    return "function"


def _constructor_name(content: bytes, definition: tree_sitter.Node) -> str:
    identifiers = tuple(
        child
        for child in definition.named_children
        if child.type in {"identifier", "type_identifier"}
    )
    if not identifiers:
        raise ValueError("Dart constructor has no extractable name.")
    return ".".join(_text(content, child) for child in identifiers)


def _signature(
    content: bytes, definition: tree_sitter.Node, kind: str
) -> str | None:
    if kind not in _CALLABLE_KINDS:
        return None
    body = definition.child_by_field_name("body")
    end_byte = body.start_byte if body is not None else definition.end_byte
    return " ".join(
        content[definition.start_byte:end_byte].decode("utf-8").split()
    )


def _drafts(content: bytes, matches: Any) -> tuple[_Draft, ...]:
    found: list[_Draft] = []
    captured: set[tuple[str, int, int]] = set()
    for _pattern, captures in matches:
        for label in _CAPTURE_KINDS:
            names = captures.get(f"{label}.name", ())
            definitions = captures.get(f"{label}.def", ())
            if not definitions or (not names and label != "constructor"):
                continue
            definition = definitions[0]
            key = (label, definition.start_byte, definition.end_byte)
            if key in captured:
                break
            captured.add(key)
            name = (
                _constructor_name(content, definition)
                if label == "constructor"
                else _text(content, names[0])
            )
            scopes = tuple(
                item
                for node in _scope_nodes(definition)
                if (item := _scope_name(content, node)) is not None
            )
            kind = _kind(label, definition)
            qualified_name = (
                name if kind == "constructor" else ".".join((*scopes, name))
            )
            found.append(
                _Draft(
                    kind=kind,
                    name=name,
                    qualified_name=qualified_name,
                    start_line=definition.start_point[0] + 1,
                    end_line=definition.end_point[0] + 1,
                    start_byte=definition.start_byte,
                    signature=_signature(content, definition, kind),
                )
            )
            break
    return tuple(sorted(found, key=lambda item: (item.start_byte, item.kind, item.name)))


def _overload_token(signature: str | None) -> str:
    value = signature or "unspecified"
    return f"sha256:{sha256(value.encode()).hexdigest()}"


def _symbols(
    snapshot: SourceSnapshot, language: str, drafts: tuple[_Draft, ...]
) -> tuple[ParsedSymbol, ...]:
    occurrences: defaultdict[tuple[str, str, str], int] = defaultdict(int)
    symbols: list[ParsedSymbol] = []
    seen_ids: set[str] = set()
    for item in drafts:
        discriminator = None
        group = (item.kind, item.qualified_name)
        if item.kind in _CALLABLE_KINDS:
            base = _overload_token(item.signature)
            occurrence_key = (*group, base)
            occurrences[occurrence_key] += 1
            occurrence = occurrences[occurrence_key]
            discriminator = base if occurrence == 1 else f"{base}#{occurrence}"
        stable_id = derive_symbol_stable_id(
            snapshot.candidate.stable_file_id,
            language,
            item.kind,
            item.qualified_name,
            discriminator,
        )
        if stable_id in seen_ids:
            continue
        seen_ids.add(stable_id)
        symbols.append(
            ParsedSymbol(
                stable_symbol_id=stable_id,
                file_stable_id=snapshot.candidate.stable_file_id,
                language=language,
                kind=item.kind,
                name=item.name,
                qualified_name=item.qualified_name,
                start_line=item.start_line,
                end_line=item.end_line,
                signature=item.signature,
                overload_discriminator=discriminator,
            )
        )
    return tuple(symbols)


def parse_snapshot(
    snapshot: SourceSnapshot, runtime: ParserRuntime | None = None
) -> ParsedFile:
    """Purely parse one immutable snapshot without persistence side effects."""
    if not isinstance(snapshot, SourceSnapshot):
        raise ValueError("snapshot must be a SourceSnapshot.")
    parser_runtime = runtime or initialize_parser_runtime(SYMBOL_QUERIES)
    extension = PurePosixPath(snapshot.candidate.relative_path).suffix.lower()
    readiness = parser_runtime.language_readiness.get(extension)
    if readiness is None:
        item = _diagnostic(
            DiagnosticSeverity.WARNING,
            "unsupported_extension",
            "Source extension is not supported.",
            snapshot,
        )
        return ParsedFile(snapshot, ParseStatus.SKIPPED, diagnostics=(item,))
    if not readiness.ready:
        runtime_item = readiness.diagnostic
        code = (
            runtime_item.code.value
            if runtime_item is not None
            else "grammar_initialization_failed"
        )
        optional = code == "grammar_module_unavailable"
        item = _diagnostic(
            DiagnosticSeverity.WARNING if optional else DiagnosticSeverity.ERROR,
            code,
            "Language grammar is unavailable." if optional else "Parser is not ready.",
            snapshot,
            runtime_item.exception_type if runtime_item is not None else None,
        )
        status = ParseStatus.SKIPPED if optional else ParseStatus.FAILED
        return ParsedFile(snapshot, status, diagnostics=(item,))
    parser = parser_runtime.parsers[extension]
    query = parser_runtime.queries[extension]
    try:
        tree = parser.parse(snapshot.content)
    except Exception as error:
        item = _diagnostic(
            DiagnosticSeverity.ERROR,
            "parse_failed",
            "Syntax tree construction failed.",
            snapshot,
            type(error).__name__,
        )
        return ParsedFile(snapshot, ParseStatus.FAILED, diagnostics=(item,))
    if tree.root_node.has_error:
        item = _diagnostic(
            DiagnosticSeverity.ERROR,
            "syntax_error",
            "Source contains invalid syntax.",
            snapshot,
        )
        return ParsedFile(snapshot, ParseStatus.FAILED, diagnostics=(item,))
    try:
        matches = tree_sitter.QueryCursor(query).matches(tree.root_node)
        symbols = _symbols(snapshot, LANGUAGE_NAMES[extension], _drafts(snapshot.content, matches))
    except Exception as error:
        code = (
            "query_execution_failed"
            if isinstance(error, RuntimeError)
            else "symbol_extraction_failed"
        )
        item = _diagnostic(
            DiagnosticSeverity.ERROR,
            code,
            "Symbol extraction failed.",
            snapshot,
            type(error).__name__,
        )
        return ParsedFile(snapshot, ParseStatus.FAILED, diagnostics=(item,))
    return ParsedFile(snapshot, ParseStatus.INDEXED, symbols=symbols)
