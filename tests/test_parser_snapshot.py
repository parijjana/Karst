from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO, cast
from uuid import NAMESPACE_URL, uuid5

import pytest

from src import parser_snapshot
from src.index_models import (
    DiagnosticSeverity,
    IndexBudget,
    IndexDiagnostic,
    SourceSnapshot,
)
from src.parser_snapshot import read_snapshot
from src.parser_symbols import parse_snapshot


PROJECT_ID = str(uuid5(NAMESPACE_URL, "project:snapshot-tests"))


def budget(max_file_bytes: int) -> IndexBudget:
    return IndexBudget(
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_file_bytes,
    )


def test_exact_file_limit_passes_and_plus_one_is_skipped(tmp_path: Path) -> None:
    exact = tmp_path / "exact.py"
    exact.write_bytes(b"x=1\n")
    too_large = tmp_path / "large.py"
    too_large.write_bytes(b"x=10\n")

    accepted = read_snapshot(exact, PROJECT_ID, "exact.py", budget(4))
    rejected = read_snapshot(too_large, PROJECT_ID, "large.py", budget(4))

    assert isinstance(accepted, SourceSnapshot)
    assert accepted.content == b"x=1\n"
    assert accepted.byte_size == 4
    assert isinstance(rejected, IndexDiagnostic)
    assert rejected.code == "file_too_large"
    assert rejected.severity is DiagnosticSeverity.WARNING
    assert rejected.relative_path == "large.py"


def test_reader_handles_short_reads_and_never_consumes_past_limit_plus_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "short.py"
    path.write_bytes(b"value = 1\n")
    real_open = Path.open
    requests: list[int] = []
    returned: list[int] = []

    class ShortReader:
        def __init__(self, handle: BinaryIO) -> None:
            self.handle = handle

        def __enter__(self) -> ShortReader:
            return self

        def __exit__(self, *args: object) -> None:
            self.handle.close()

        def fileno(self) -> int:
            return self.handle.fileno()

        def read(self, size: int) -> bytes:
            requests.append(size)
            chunk = self.handle.read(min(size, 2))
            returned.append(len(chunk))
            return chunk

    def short_open(target: Path, *args: object, **kwargs: object) -> ShortReader:
        del args, kwargs
        return ShortReader(cast(BinaryIO, real_open(target, "rb")))

    monkeypatch.setattr(Path, "open", short_open)

    result = read_snapshot(path, PROJECT_ID, "short.py", budget(10))

    assert isinstance(result, SourceSnapshot)
    assert result.content == b"value = 1\n"
    assert requests and max(requests) <= 11
    assert sum(returned) == 10


def test_open_handle_bytes_drive_both_hash_and_parse_after_path_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "replace.py"
    path.write_bytes(b"class Original:\n    pass\n")
    stat_handle = (tmp_path / "stable-handle").open("w+b")
    original = b"class Original:\n    pass\n"
    real_open = Path.open

    class ReplacedPathReader:
        def __init__(self) -> None:
            self.offset = 0
            self.replaced = False

        def __enter__(self) -> ReplacedPathReader:
            return self

        def __exit__(self, *args: object) -> None:
            stat_handle.close()

        def fileno(self) -> int:
            return stat_handle.fileno()

        def read(self, size: int) -> bytes:
            if not self.replaced:
                self.replaced = True
                with real_open(path, "wb") as replacement:
                    replacement.write(b"class Replacement:\n    pass\n")
            chunk = original[self.offset : self.offset + size]
            self.offset += len(chunk)
            return chunk

    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: ReplacedPathReader())

    result = read_snapshot(path, PROJECT_ID, "replace.py", budget(128))

    assert isinstance(result, SourceSnapshot)
    parsed = parse_snapshot(result)
    assert result.content_sha256 == hashlib.sha256(original).hexdigest()
    assert [symbol.name for symbol in parsed.symbols] == ["Original"]
    with real_open(path, "rb") as replacement:
        assert b"Replacement" in replacement.read()


def test_in_place_mutation_is_rejected_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "mutated.py"
    path.write_bytes(b"value = 1\n")
    real_fstat = parser_snapshot.os.fstat
    calls = 0

    def changed_fstat(fd: int) -> object:
        nonlocal calls
        calls += 1
        value = real_fstat(fd)
        if calls == 1:
            return value
        return SimpleNamespace(
            st_dev=value.st_dev,
            st_ino=value.st_ino,
            st_mode=value.st_mode,
            st_size=value.st_size,
            st_mtime_ns=value.st_mtime_ns + 1,
            st_ctime_ns=value.st_ctime_ns,
        )

    monkeypatch.setattr(parser_snapshot.os, "fstat", changed_fstat)

    result = read_snapshot(path, PROJECT_ID, "mutated.py", budget(64))

    assert isinstance(result, IndexDiagnostic)
    assert result.code == "source_changed_during_read"
    assert result.severity is DiagnosticSeverity.ERROR


def test_over_cap_relative_path_fails_before_opening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ordinary.py"
    path.write_bytes(b"value = 1\n")

    def forbidden_open(*args: object, **kwargs: object) -> object:
        raise AssertionError("invalid identity must fail before opening")

    monkeypatch.setattr(Path, "open", forbidden_open)

    result = read_snapshot(path, PROJECT_ID, f"{'x' * 4094}.py", budget(64))

    assert isinstance(result, IndexDiagnostic)
    assert result.code == "invalid_source_identity"
    assert result.severity is DiagnosticSeverity.ERROR
    assert result.relative_path is None


def test_reader_rejects_a_chunk_larger_than_requested_without_snapshotting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "over-return.py"
    path.write_bytes(b"value = 1\n")
    real_open = Path.open
    requests: list[int] = []

    class OverReturningReader:
        def __init__(self, handle: BinaryIO) -> None:
            self.handle = handle

        def __enter__(self) -> OverReturningReader:
            return self

        def __exit__(self, *args: object) -> None:
            self.handle.close()

        def fileno(self) -> int:
            return self.handle.fileno()

        def read(self, size: int) -> bytes:
            requests.append(size)
            return b"x" * (size + 1)

    def malicious_open(
        target: Path, *args: object, **kwargs: object
    ) -> OverReturningReader:
        del args, kwargs
        return OverReturningReader(cast(BinaryIO, real_open(target, "rb")))

    monkeypatch.setattr(Path, "open", malicious_open)

    result = read_snapshot(path, PROJECT_ID, "over-return.py", budget(64))

    assert isinstance(result, IndexDiagnostic)
    assert result.code == "file_read_failed"
    assert result.severity is DiagnosticSeverity.ERROR
    assert requests == [65]
