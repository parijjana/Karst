from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from uuid import NAMESPACE_URL, UUID, uuid5

from src.karst_core.database.db_migration_support import SchemaUpgradeError
LEGACY_PROJECT_NAMESPACE = uuid5(NAMESPACE_URL, "karst:legacy-project:v3")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_IDENTITY_TOKEN = re.compile(r"[^a-z0-9_+-]+")


LANGUAGE_BY_SUFFIX = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".cxx": "cpp",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".php": "php",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".swift": "swift",
    ".ts": "typescript",
    ".tsx": "typescript",
}


@dataclass(frozen=True, slots=True)
class LegacyFileIdentity:
    relative_path: str
    stable_id: str
    language: str


def project_identity_namespace(
    project_id: int,
    name: str,
    root: str,
    stable_id: str | None,
) -> str:
    if stable_id is not None:
        try:
            candidate = UUID(stable_id)
        except ValueError:
            candidate = None
        if (
            candidate is not None
            and candidate.version == 5
            and str(candidate) == stable_id
        ):
            return stable_id
        material = json.dumps(["stable", stable_id], separators=(",", ":"))
    else:
        material = json.dumps(
            ["quarantined", project_id, name, root], separators=(",", ":")
        )
    return str(uuid5(LEGACY_PROJECT_NAMESPACE, material))


def derive_legacy_file_identity(
    project_id: int,
    project_name: str,
    project_root: str,
    project_stable_id: str | None,
    absolute_path: str,
) -> LegacyFileIdentity:
    relative_path, windows = lexical_relative_path(project_root, absolute_path)
    identity_path = relative_path.casefold() if windows else relative_path
    namespace = project_identity_namespace(
        project_id, project_name, project_root, project_stable_id
    )
    return LegacyFileIdentity(
        relative_path,
        str(uuid5(UUID(namespace), identity_path)),
        infer_language(relative_path),
    )


def derive_legacy_symbol_id(
    file_stable_id: str,
    language: str,
    kind: str,
    qualified_name: str,
    overload_discriminator: str | None,
) -> str:
    identity = json.dumps(
        [
            _legacy_token(language),
            _legacy_token(kind),
            qualified_name,
            overload_discriminator,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return str(uuid5(UUID(file_stable_id), identity))


def infer_language(relative_path: str) -> str:
    suffix = PurePosixPath(relative_path).suffix.lower()
    return LANGUAGE_BY_SUFFIX.get(suffix, "unknown")


def compatibility_absolute_path(project_root: str, path: str) -> str:
    """Expand a legacy repository-relative path without consulting the filesystem."""
    if _is_windows_path(path) or PurePosixPath(path).is_absolute():
        return path
    if PureWindowsPath(path).drive or "\x00" in path:
        raise SchemaUpgradeError("Compatibility file path is invalid.")
    if _is_windows_path(project_root):
        return str(PureWindowsPath(project_root).joinpath(PureWindowsPath(path)))
    if "\\" in path:
        raise SchemaUpgradeError("Compatibility file path is invalid.")
    return str(PurePosixPath(project_root).joinpath(PurePosixPath(path)))


def lexical_relative_path(project_root: str, absolute_path: str) -> tuple[str, bool]:
    windows_root = _is_windows_path(project_root)
    windows_file = _is_windows_path(absolute_path)
    if windows_root != windows_file:
        raise SchemaUpgradeError(
            "File path is not lexically contained by project root."
        )
    if windows_root:
        root_anchor, root_parts = _windows_parts(project_root)
        file_anchor, file_parts = _windows_parts(absolute_path)
        if root_anchor.casefold() != file_anchor.casefold():
            raise SchemaUpgradeError(
                "File path is not lexically contained by project root."
            )
        comparison_root = tuple(part.casefold() for part in root_parts)
        comparison_file = tuple(part.casefold() for part in file_parts)
    else:
        root_anchor, root_parts = _posix_parts(project_root)
        file_anchor, file_parts = _posix_parts(absolute_path)
        if root_anchor != file_anchor:
            raise SchemaUpgradeError(
                "File path is not lexically contained by project root."
            )
        comparison_root = root_parts
        comparison_file = file_parts
    if comparison_file == comparison_root:
        raise SchemaUpgradeError("File path cannot equal the project root.")
    if comparison_file[: len(comparison_root)] != comparison_root:
        raise SchemaUpgradeError(
            "File path is not lexically contained by project root."
        )
    relative_parts = file_parts[len(root_parts) :]
    if not relative_parts:
        raise SchemaUpgradeError("File path cannot equal the project root.")
    return "/".join(relative_parts), windows_root


def _is_windows_path(value: str) -> bool:
    return bool(_WINDOWS_DRIVE.match(value)) or value.startswith(("\\\\", "//"))


def _windows_parts(value: str) -> tuple[str, tuple[str, ...]]:
    parsed = PureWindowsPath(value)
    if not parsed.is_absolute() or not parsed.anchor:
        raise SchemaUpgradeError("Project and file paths must be absolute.")
    return parsed.anchor, _collapse_parts(parsed.parts[1:])


def _posix_parts(value: str) -> tuple[str, tuple[str, ...]]:
    parsed = PurePosixPath(value)
    if not parsed.is_absolute() or parsed.anchor != "/":
        raise SchemaUpgradeError("Project and file paths must be absolute.")
    return parsed.anchor, _collapse_parts(parsed.parts[1:])


def _collapse_parts(parts: tuple[str, ...]) -> tuple[str, ...]:
    collapsed: list[str] = []
    for part in parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not collapsed:
                raise SchemaUpgradeError("Path lexically escapes its absolute root.")
            collapsed.pop()
        else:
            collapsed.append(part)
    return tuple(collapsed)


def _legacy_token(value: str) -> str:
    token = _IDENTITY_TOKEN.sub("_", value.strip().lower()).strip("_")
    if not token or not token[0].isalpha():
        token = f"legacy_{token}" if token else "legacy"
    return token[:64]
