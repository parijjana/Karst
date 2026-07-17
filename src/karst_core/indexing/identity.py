"""Stable graph identities owned by the Karst data core."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from uuid import UUID, uuid5

from src.text_contracts import (
    require_kind,
    require_language,
    require_name,
    require_overload_discriminator,
    require_qualified_name,
    require_relative_posix_path,
    require_signature,
)


__all__ = [
    "FileCandidate",
    "ParsedSymbol",
    "SourceSnapshot",
    "derive_file_stable_id",
    "derive_symbol_stable_id",
]


_require_relative_posix_path = require_relative_posix_path


def _require_uuid5(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a canonical UUIDv5 string.")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError(f"{field} must be a canonical UUIDv5 string.") from error
    if parsed.version != 5 or str(parsed) != value:
        raise ValueError(f"{field} must be a canonical UUIDv5 string.")
    return value


def _require_positive_line(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def derive_file_stable_id(project_stable_id: str, identity_path: str) -> str:
    """Derive a file UUIDv5 from its canonical project-relative birth path."""
    project_id = _require_uuid5(project_stable_id, "project_stable_id")
    canonical_path = _require_relative_posix_path(identity_path)
    return str(uuid5(UUID(project_id), canonical_path))


def derive_symbol_stable_id(
    file_stable_id: str,
    language: str,
    kind: str,
    qualified_name: str,
    overload_discriminator: str | None,
) -> str:
    """Derive semantic symbol identity; source ranges and signatures are excluded."""
    file_id = _require_uuid5(file_stable_id, "file_stable_id")
    canonical_language = require_language(language)
    canonical_kind = require_kind(kind)
    canonical_name = require_qualified_name(qualified_name)
    overload = (
        None
        if overload_discriminator is None
        else require_overload_discriminator(overload_discriminator)
    )
    identity = json.dumps(
        [canonical_language, canonical_kind, canonical_name, overload],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return str(uuid5(UUID(file_id), identity))


@dataclass(frozen=True, slots=True, init=False)
class FileCandidate:
    """A current path plus the immutable birth path that owns file identity."""

    project_stable_id: str
    relative_path: str
    stable_file_id: str
    identity_path: str

    def __init__(
        self,
        project_stable_id: str,
        relative_path: str,
        stable_file_id: str,
        identity_path: str | None = None,
    ) -> None:
        project_id = _require_uuid5(project_stable_id, "project_stable_id")
        current_path = _require_relative_posix_path(relative_path)
        birth_path = (
            current_path
            if identity_path is None
            else _require_relative_posix_path(identity_path)
        )
        file_id = _require_uuid5(stable_file_id, "stable_file_id")
        expected = derive_file_stable_id(project_id, birth_path)
        if file_id != expected:
            raise ValueError(
                "stable_file_id does not match project and identity_path."
            )
        object.__setattr__(self, "project_stable_id", project_id)
        object.__setattr__(self, "relative_path", current_path)
        object.__setattr__(self, "stable_file_id", file_id)
        object.__setattr__(self, "identity_path", birth_path)

    @classmethod
    def for_new_file(
        cls, project_stable_id: str, relative_path: str
    ) -> FileCandidate:
        stable_id = derive_file_stable_id(project_stable_id, relative_path)
        return cls(project_stable_id, relative_path, stable_id)

    @classmethod
    def for_content_preserving_rename(
        cls, previous: FileCandidate, relative_path: str
    ) -> FileCandidate:
        if not isinstance(previous, FileCandidate):
            raise ValueError("previous must be a FileCandidate.")
        return cls(
            previous.project_stable_id,
            relative_path,
            previous.stable_file_id,
            identity_path=previous.identity_path,
        )


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """Immutable source bytes with metadata computed from exactly those bytes."""

    candidate: FileCandidate
    content: bytes
    byte_size: int = field(init=False)
    content_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, FileCandidate):
            raise ValueError("candidate must be a FileCandidate.")
        if not isinstance(self.content, bytes):
            raise ValueError("content must be immutable bytes.")
        object.__setattr__(self, "byte_size", len(self.content))
        object.__setattr__(self, "content_sha256", sha256(self.content).hexdigest())


@dataclass(frozen=True, slots=True)
class ParsedSymbol:
    """Semantic symbol identity plus non-identity display and source metadata."""

    stable_symbol_id: str
    file_stable_id: str
    language: str
    kind: str
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    signature: str | None = None
    overload_discriminator: str | None = None

    def __post_init__(self) -> None:
        require_name(self.name)
        if self.signature is not None:
            require_signature(self.signature)
        expected = derive_symbol_stable_id(
            self.file_stable_id,
            self.language,
            self.kind,
            self.qualified_name,
            self.overload_discriminator,
        )
        if self.stable_symbol_id != expected:
            raise ValueError("stable_symbol_id does not match semantic identity.")
        start = _require_positive_line(self.start_line, "start_line")
        end = _require_positive_line(self.end_line, "end_line")
        if end < start:
            raise ValueError("end_line must not precede start_line.")
