from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from src.database import Database
from src.index_identity import derive_file_stable_id
from src.index_models import (
    DiagnosticSeverity,
    FileCandidate,
    ParsedFile,
    ParseStatus,
    SourceSnapshot,
)
from src.parser_symbols import parse_snapshot


PROJECT_ID = str(uuid5(NAMESPACE_URL, "project:symbol-tests"))


def snapshot(relative_path: str, content: bytes) -> SourceSnapshot:
    candidate = FileCandidate(
        PROJECT_ID,
        relative_path,
        derive_file_stable_id(PROJECT_ID, relative_path),
    )
    return SourceSnapshot(candidate, content)


def test_python_qualified_symbols_and_overloads_are_exact_and_distinct() -> None:
    parsed = parse_snapshot(
        snapshot(
            "nested.py",
            b"""class Outer:
    class Inner:
        @overload
        def run(self, value: int) -> int: ...
        @overload
        def run(self, value: str) -> str: ...

    def wrap(self):
        def local(value: int):
            return value
""",
        )
    )

    assert parsed.status is ParseStatus.INDEXED
    identities = {(item.kind, item.qualified_name) for item in parsed.symbols}
    assert identities >= {
        ("class", "Outer"),
        ("class", "Outer.Inner"),
        ("method", "Outer.Inner.run"),
        ("method", "Outer.wrap"),
        ("function", "Outer.wrap.local"),
    }
    overloads = [
        item for item in parsed.symbols if item.qualified_name == "Outer.Inner.run"
    ]
    assert len(overloads) == 2
    assert len({item.stable_symbol_id for item in overloads}) == 2
    assert len({item.overload_discriminator for item in overloads}) == 2
    assert all(item.signature for item in overloads)
    assert all(item.language == "python" for item in parsed.symbols)


def test_typescript_and_javascript_methods_keep_lexical_qualification() -> None:
    typescript = parse_snapshot(
        snapshot(
            "service.ts",
            b"""namespace Api {
class Service {
  run(value: string): string;
  run(value: number): number;
  run(value: string | number) { return value; }
}
function outer() { function inner(): void {} }
}
""",
        )
    )
    javascript = parse_snapshot(
        snapshot(
            "service.js",
            b"""class Service {
  run(value) { function local() {} }
}
function outer() { function inner() {} }
""",
        )
    )

    ts_overloads = [
        item for item in typescript.symbols if item.qualified_name == "Api.Service.run"
    ]
    assert len(ts_overloads) == 3
    assert {item.kind for item in ts_overloads} == {"method"}
    assert len({item.stable_symbol_id for item in ts_overloads}) == 3
    assert len({item.overload_discriminator for item in ts_overloads}) == 3
    assert ("function", "Api.outer.inner") in {
        (item.kind, item.qualified_name) for item in typescript.symbols
    }
    assert {item.language for item in typescript.symbols} == {"typescript"}
    assert {item.language for item in javascript.symbols} == {"javascript"}
    assert ("method", "Service.run") in {
        (item.kind, item.qualified_name) for item in javascript.symbols
    }
    assert ("function", "Service.run.local") in {
        (item.kind, item.qualified_name) for item in javascript.symbols
    }


def test_blank_line_movement_changes_ranges_but_not_symbol_ids() -> None:
    source = b"class Example:\n    def run(self, value: int):\n        return value\n"
    original = parse_snapshot(snapshot("stable.py", source))
    moved = parse_snapshot(snapshot("stable.py", b"\n\n\n" + source))

    original_by_name = {
        (item.kind, item.qualified_name, item.overload_discriminator): item
        for item in original.symbols
    }
    moved_by_name = {
        (item.kind, item.qualified_name, item.overload_discriminator): item
        for item in moved.symbols
    }
    assert original_by_name.keys() == moved_by_name.keys()
    assert {
        key: item.stable_symbol_id for key, item in original_by_name.items()
    } == {key: item.stable_symbol_id for key, item in moved_by_name.items()}
    assert moved_by_name[("class", "Example", None)].start_line == 4


def test_pure_parse_never_writes_to_the_database() -> None:
    db = Database(":memory:")
    try:
        parsed = parse_snapshot(snapshot("pure.py", b"class Pure:\n    pass\n"))
        files = db.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        nodes = db.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    finally:
        db.close()

    assert isinstance(parsed, ParsedFile)
    assert parsed.status is ParseStatus.INDEXED
    assert (files, nodes) == (0, 0)


def test_parse_failures_and_skips_respect_frozen_contract_severity() -> None:
    unsupported = parse_snapshot(snapshot("notes.txt", b"plain text"))
    invalid = parse_snapshot(snapshot("invalid.py", b"def broken(:\n"))

    assert unsupported.status is ParseStatus.SKIPPED
    assert unsupported.diagnostics[0].severity is DiagnosticSeverity.WARNING
    assert invalid.status is ParseStatus.FAILED
    assert invalid.diagnostics[0].severity is DiagnosticSeverity.ERROR


def test_markdown_heading_behavior_is_preserved() -> None:
    parsed = parse_snapshot(snapshot("README.md", b"# Heading\n"))

    assert parsed.status is ParseStatus.INDEXED
    assert [(item.kind, item.name) for item in parsed.symbols] == [
        ("heading", "Heading")
    ]


def test_over_cap_name_and_signature_fail_instead_of_truncating() -> None:
    long_name = "名" * 172
    name_result = parse_snapshot(
        snapshot("long-name.py", f"class {long_name}:\n    pass\n".encode())
    )
    parameters = ", ".join(f"parameter_{index}: int" for index in range(130))
    signature_result = parse_snapshot(
        snapshot(
            "long-signature.py",
            f"def oversized({parameters}) -> int:\n    return 1\n".encode(),
        )
    )

    assert name_result.status is ParseStatus.FAILED
    assert signature_result.status is ParseStatus.FAILED
    assert name_result.symbols == signature_result.symbols == ()
    assert name_result.diagnostics[0].code == "symbol_extraction_failed"
    assert signature_result.diagnostics[0].code == "symbol_extraction_failed"


def test_real_dart_snapshot_extracts_only_supported_qualified_constructs() -> None:
    parsed = parse_snapshot(
        snapshot(
            "greeter.dart",
            b"""class Greeter {
  Greeter(this.name);
  const Greeter.named(this.name);
  factory Greeter.make(String name) => Greeter(name);
  final String name;
  String greet(String who) { return '$who $name'; }
}
int topLevel(int value) => value;
""",
        )
    )

    assert parsed.status is ParseStatus.INDEXED
    assert {
        (item.kind, item.name, item.qualified_name) for item in parsed.symbols
    } == {
        ("class", "Greeter", "Greeter"),
        ("constructor", "Greeter", "Greeter"),
        ("constructor", "Greeter.make", "Greeter.make"),
        ("constructor", "Greeter.named", "Greeter.named"),
        ("method", "greet", "Greeter.greet"),
        ("function", "topLevel", "topLevel"),
    }
    assert {item.language for item in parsed.symbols} == {"dart"}
    assert all(
        item.overload_discriminator is not None
        for item in parsed.symbols
        if item.kind != "class"
    )


def test_typescript_overload_evolution_preserves_the_existing_declaration_id() -> None:
    singleton = parse_snapshot(
        snapshot(
            "evolution.ts",
            b"""class Service {
  run(value: string): string { return value; }
}
""",
        )
    )
    evolved = parse_snapshot(
        snapshot(
            "evolution.ts",
            b"""class Service {
  run(value: number): number;
  run(value: string): string { return value; }
}
""",
        )
    )

    original = next(item for item in singleton.symbols if item.kind == "method")
    unchanged = next(
        item for item in evolved.symbols if item.signature == original.signature
    )
    assert original.start_line != unchanged.start_line
    assert original.overload_discriminator == unchanged.overload_discriminator
    assert original.stable_symbol_id == unchanged.stable_symbol_id


def test_python_overload_evolution_preserves_the_existing_declaration_id() -> None:
    singleton = parse_snapshot(
        snapshot(
            "evolution.py",
            b"def run(value: str) -> str:\n    return value\n",
        )
    )
    evolved = parse_snapshot(
        snapshot(
            "evolution.py",
            b"""@overload
def run(value: int) -> int: ...
def run(value: str) -> str:
    return value
""",
        )
    )

    original = singleton.symbols[0]
    unchanged = next(
        item for item in evolved.symbols if item.signature == original.signature
    )
    assert original.start_line != unchanged.start_line
    assert original.overload_discriminator == unchanged.overload_discriminator
    assert original.stable_symbol_id == unchanged.stable_symbol_id


def test_normalized_signature_changes_intentionally_change_callable_identity() -> None:
    integer = parse_snapshot(
        snapshot("signature.py", b"def run(value: int) -> int:\n    return value\n")
    ).symbols[0]
    text = parse_snapshot(
        snapshot("signature.py", b"def run(value: str) -> str:\n    return value\n")
    ).symbols[0]

    assert integer.qualified_name == text.qualified_name == "run"
    assert integer.signature == "def run(value: int) -> int:"
    assert text.signature == "def run(value: str) -> str:"
    assert integer.overload_discriminator != text.overload_discriminator
    assert integer.stable_symbol_id != text.stable_symbol_id
