from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from src import index_identity
from src.index_identity import derive_file_stable_id, derive_symbol_stable_id
from src.index_models import FileCandidate, ParsedSymbol, SourceSnapshot
from src.text_contracts import (
    MAX_NAME_UTF8_BYTES,
    MAX_OVERLOAD_DISCRIMINATOR_UTF8_BYTES,
    MAX_QUALIFIED_NAME_UTF8_BYTES,
    MAX_RELATIVE_PATH_UTF8_BYTES,
    MAX_SIGNATURE_UTF8_BYTES,
)


PROJECT_ID = str(uuid5(NAMESPACE_URL, "project:/workspace"))
FILE_ID = derive_file_stable_id(PROJECT_ID, "src/example.py")
SYMBOL_ID = derive_symbol_stable_id(
    FILE_ID, "python", "class", "example.Example", None
)


def candidate(**overrides: Any) -> FileCandidate:
    values: dict[str, Any] = {
        "project_stable_id": PROJECT_ID,
        "relative_path": "src/example.py",
        "stable_file_id": FILE_ID,
    }
    values.update(overrides)
    return FileCandidate(**values)


def symbol(**overrides: Any) -> ParsedSymbol:
    values: dict[str, Any] = {
        "stable_symbol_id": SYMBOL_ID,
        "file_stable_id": FILE_ID,
        "language": "python",
        "kind": "class",
        "name": "Example",
        "qualified_name": "example.Example",
        "start_line": 1,
        "end_line": 2,
    }
    values.update(overrides)
    return ParsedSymbol(**values)


def test_file_identity_is_uuid5_of_project_and_canonical_relative_path() -> None:
    expected = str(uuid5(UUID(PROJECT_ID), "src/example.py"))

    assert derive_file_stable_id(PROJECT_ID, "src/example.py") == expected
    assert derive_file_stable_id(PROJECT_ID, "src/example.py") == expected


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/absolute.py",
        "C:/absolute.py",
        "C:drive-relative.py",
        "../escape.py",
        "src/../escape.py",
        "src\\windows.py",
        "./src/example.py",
        "src//example.py",
        "src/example.py/",
    ],
)
def test_file_identity_rejects_noncanonical_or_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError, match="relative POSIX path"):
        derive_file_stable_id(PROJECT_ID, path)


def test_symbol_identity_uses_semantic_identity_but_excludes_source_ranges() -> None:
    file_id = derive_file_stable_id(PROJECT_ID, "src/example.py")
    first = derive_symbol_stable_id(
        file_id, "python", "function", "example.run", "sync"
    )
    repeated = derive_symbol_stable_id(
        file_id, "python", "function", "example.run", "sync"
    )

    assert first == repeated
    assert UUID(first).version == 5
    assert derive_symbol_stable_id(
        file_id, "python", "function", "example.run", "async"
    ) != first
    assert derive_symbol_stable_id(
        file_id, "typescript", "function", "example.run", "sync"
    ) != first


def test_symbol_identity_components_are_unambiguous() -> None:
    file_id = derive_file_stable_id(PROJECT_ID, "src/example.py")

    assert derive_symbol_stable_id(
        file_id, "python", "function", "a|b", "c"
    ) != derive_symbol_stable_id(file_id, "python", "function", "a", "b|c")


def test_candidate_verifies_derived_identity_not_absolute_path_identity() -> None:
    absolute_derived = str(uuid5(UUID(PROJECT_ID), "D:/repo/src/example.py"))

    with pytest.raises(ValueError, match="stable_file_id"):
        candidate(stable_file_id=absolute_derived)


def test_new_file_factory_sets_birth_identity_to_the_current_path() -> None:
    created = FileCandidate.for_new_file(PROJECT_ID, "src/new.py")
    legacy_constructor = candidate()

    assert created.identity_path == created.relative_path == "src/new.py"
    assert created.stable_file_id == derive_file_stable_id(PROJECT_ID, "src/new.py")
    assert legacy_constructor.identity_path == legacy_constructor.relative_path
    with pytest.raises(FrozenInstanceError):
        created.identity_path = "src/forged.py"  # type: ignore[misc]


def test_explicit_content_preserving_rename_carries_birth_identity_and_id() -> None:
    original = FileCandidate.for_new_file(PROJECT_ID, "src/original.py")
    renamed = FileCandidate.for_content_preserving_rename(
        original, "src/renamed.py"
    )
    original_snapshot = SourceSnapshot(original, b"same content")
    renamed_snapshot = SourceSnapshot(renamed, b"different content is metadata only")

    assert renamed.relative_path == "src/renamed.py"
    assert renamed.identity_path == original.identity_path == "src/original.py"
    assert renamed.stable_file_id == original.stable_file_id
    assert original_snapshot.candidate.stable_file_id == (
        renamed_snapshot.candidate.stable_file_id
    )
    assert original_snapshot.content_sha256 != renamed_snapshot.content_sha256


def test_old_id_requires_the_matching_explicit_birth_identity() -> None:
    original = FileCandidate.for_new_file(PROJECT_ID, "src/original.py")

    with pytest.raises(ValueError, match="stable_file_id"):
        FileCandidate(PROJECT_ID, "src/renamed.py", original.stable_file_id)
    carried = FileCandidate(
        PROJECT_ID,
        "src/renamed.py",
        original.stable_file_id,
        identity_path=original.identity_path,
    )
    assert carried == FileCandidate.for_content_preserving_rename(
        original, "src/renamed.py"
    )


def test_distinct_current_paths_do_not_infer_a_rename() -> None:
    first = FileCandidate.for_new_file(PROJECT_ID, "src/first.py")
    second = FileCandidate.for_new_file(PROJECT_ID, "src/second.py")

    assert first.identity_path != second.identity_path
    assert first.stable_file_id != second.stable_file_id


@pytest.mark.parametrize("path", ["/absolute.py", "../escape.py", "src/../bad.py"])
def test_candidate_validates_both_current_and_birth_paths(path: str) -> None:
    stable_id = derive_file_stable_id(PROJECT_ID, "src/birth.py")

    with pytest.raises(ValueError, match="relative POSIX path"):
        FileCandidate(
            PROJECT_ID,
            path,
            stable_id,
            identity_path="src/birth.py",
        )
    with pytest.raises(ValueError, match="relative POSIX path"):
        FileCandidate(
            PROJECT_ID,
            "src/current.py",
            stable_id,
            identity_path=path,
        )


def test_candidate_factories_require_uuid5_project_and_a_real_prior_candidate() -> None:
    with pytest.raises(ValueError, match="UUIDv5"):
        FileCandidate.for_new_file("not-a-project-id", "src/a.py")
    with pytest.raises(ValueError, match="FileCandidate"):
        FileCandidate.for_content_preserving_rename(
            cast(Any, "src/original.py"), "src/renamed.py"
        )


def test_snapshot_computes_metadata_and_replace_recomputes_consistently() -> None:
    original = SourceSnapshot(candidate(), b"first")
    replacement = replace(original, content=b"second payload")

    assert original.byte_size == 5
    assert original.content_sha256 == sha256(b"first").hexdigest()
    assert replacement.candidate is original.candidate
    assert replacement.byte_size == len(b"second payload")
    assert replacement.content_sha256 == sha256(b"second payload").hexdigest()
    assert replacement.content_sha256 != original.content_sha256
    with pytest.raises(ValueError, match="immutable bytes"):
        SourceSnapshot(candidate(), cast(Any, bytearray(b"mutable")))


def test_symbol_identity_is_stable_across_line_and_signature_changes() -> None:
    moved = symbol(start_line=100, end_line=120, signature="Example(value)")
    original = symbol(signature="Example()")

    assert moved.stable_symbol_id == original.stable_symbol_id == SYMBOL_ID


@pytest.mark.parametrize(
    "overrides",
    [
        {"language": "typescript"},
        {"kind": "function"},
        {"qualified_name": "other.Example"},
        {"overload_discriminator": "named"},
    ],
)
def test_symbol_rejects_stable_id_mismatches(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="stable_symbol_id"):
        symbol(**overrides)


def test_stable_ids_are_not_derived_from_text_over_the_byte_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def forbidden(*_args: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("uuid5 must follow contract validation")

    monkeypatch.setattr(index_identity, "uuid5", forbidden)
    oversized_path = "é" * (MAX_RELATIVE_PATH_UTF8_BYTES // 2) + "x"
    oversized_name = "é" * (MAX_QUALIFIED_NAME_UTF8_BYTES // 2) + "x"

    with pytest.raises(ValueError, match="relative POSIX path"):
        derive_file_stable_id(PROJECT_ID, oversized_path)
    with pytest.raises(ValueError, match="UTF-8 byte limit"):
        derive_symbol_stable_id(FILE_ID, "python", "class", oversized_name, None)
    assert calls == 0


def test_stable_ids_accept_exact_multibyte_boundaries() -> None:
    exact_path = "é" * 2047 + "ab"
    exact_qualified = "é" * (MAX_QUALIFIED_NAME_UTF8_BYTES // 2)
    exact_overload = "é" * (MAX_OVERLOAD_DISCRIMINATOR_UTF8_BYTES // 2)
    file_id = derive_file_stable_id(PROJECT_ID, exact_path)
    symbol_id = derive_symbol_stable_id(
        file_id, "python", "function", exact_qualified, exact_overload
    )

    assert UUID(file_id).version == 5
    assert UUID(symbol_id).version == 5


def test_parsed_symbol_uses_query_ready_name_and_signature_byte_caps() -> None:
    exact_name = "é" * (MAX_NAME_UTF8_BYTES // 2)
    exact_signature = "é" * (MAX_SIGNATURE_UTF8_BYTES // 2)

    accepted = symbol(name=exact_name, signature=exact_signature)
    assert accepted.name == exact_name
    assert accepted.signature == exact_signature
    with pytest.raises(ValueError, match="UTF-8 byte limit"):
        symbol(name=exact_name + "é")
    with pytest.raises(ValueError, match="UTF-8 byte limit"):
        symbol(signature=exact_signature + "é")
