from __future__ import annotations

import os
import re
import stat
import uuid
from pathlib import Path
from typing import Iterable

from src.core_settings import TRUSTED_LOCAL_OWNER


_PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_PROJECT_NAMESPACE = uuid.UUID("c52ddc3b-9252-4e1d-92d4-8d884059067c")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_TRANSIENT_GENERATED_DIRECTORY = re.compile(
    r"(?:"
    r"(?:idx\d+|parser)-(?:[a-z0-9]+-)*[a-z0-9]+-\d{4,}|"
    r"kgt-[A-Za-z0-9_.-]+|"
    r"pytest-basetemp|"
    r"pytest-[A-Za-z0-9_.-]+|"
    r"terra-rereview|"
    r"Programmingcodex\.tmpcode-graph-wave\d+-gate-[A-Za-z0-9TZ.-]+pytest-basetemp|"
    r"tmp[A-Za-z0-9_]{8,}"
    r")\Z"
)


class SecurityViolation(ValueError):
    """A bounded, non-sensitive trust-boundary rejection."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def security_error(error: SecurityViolation) -> str:
    return f"Security error [{error.code}]."


def validate_project_name(name: str) -> str:
    if not _PROJECT_NAME.fullmatch(name):
        raise SecurityViolation("invalid_project_identifier")
    return name


def stable_project_id(canonical_root: Path) -> str:
    identity = f"{TRUSTED_LOCAL_OWNER}\0{os.path.normcase(str(canonical_root))}"
    return str(uuid.uuid5(_PROJECT_NAMESPACE, identity))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_reparse_point(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError as error:
        raise SecurityViolation("path_unavailable") from error
    return stat.S_ISLNK(details.st_mode) or bool(
        getattr(details, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _is_transient_generated_directory(name: str) -> bool:
    """Recognize local test/review artifacts without weakening normal traversal."""
    return bool(_TRANSIENT_GENERATED_DIRECTORY.fullmatch(name))


class PathSecurityPolicy:
    """Fail-closed containment and reparse-point policy for repository input."""

    def __init__(self, allowed_roots: Iterable[Path]) -> None:
        try:
            self.allowed_roots = tuple(
                Path(root).resolve(strict=True) for root in allowed_roots
            )
        except OSError as error:
            raise SecurityViolation("allowed_roots_unavailable") from error
        if not self.allowed_roots:
            raise SecurityViolation("allowed_roots_unavailable")

    def validate_project_root(self, supplied: str | Path) -> Path:
        lexical = self._absolute_lexical(supplied, relative_base=None)
        allowed_root = self._lexical_allowed_root(lexical)
        self._reject_reparse_descendants(allowed_root, lexical)
        try:
            canonical = lexical.resolve(strict=True)
        except OSError as error:
            raise SecurityViolation("path_unavailable") from error
        if not canonical.is_dir():
            raise SecurityViolation("project_root_invalid")
        if not _is_relative_to(canonical, allowed_root):
            raise SecurityViolation("path_not_allowed")
        return canonical

    def validate_project_file(
        self, supplied: str | Path, canonical_project_root: str | Path
    ) -> Path:
        try:
            project_root = Path(canonical_project_root).resolve(strict=True)
        except OSError as error:
            raise SecurityViolation("project_identity_invalid") from error
        if not any(_is_relative_to(project_root, root) for root in self.allowed_roots):
            raise SecurityViolation("project_boundary_violation")
        lexical = self._absolute_lexical(supplied, relative_base=project_root)
        if not _is_relative_to(lexical, project_root):
            raise SecurityViolation("project_boundary_violation")
        self._reject_reparse_descendants(project_root, lexical)
        try:
            canonical = lexical.resolve(strict=True)
        except OSError as error:
            raise SecurityViolation("path_unavailable") from error
        if not _is_relative_to(canonical, project_root):
            raise SecurityViolation("project_boundary_violation")
        if not canonical.is_file():
            raise SecurityViolation("project_file_invalid")
        return canonical

    def discover_project_files(
        self,
        canonical_project_root: Path,
        valid_extensions: set[str],
        ignored_directories: set[str],
    ) -> list[Path]:
        discovered: list[Path] = []

        def fail_on_walk_error(error: OSError) -> None:
            raise SecurityViolation("path_unavailable") from error

        for current_root, directory_names, file_names in os.walk(
            canonical_project_root,
            topdown=True,
            onerror=fail_on_walk_error,
            followlinks=False,
        ):
            current = Path(current_root)
            retained_directories: list[str] = []
            for name in directory_names:
                if (
                    name in ignored_directories
                    or name.startswith(".")
                    or _is_transient_generated_directory(name)
                ):
                    continue
                candidate = current / name
                if _is_reparse_point(candidate):
                    raise SecurityViolation("link_not_allowed")
                retained_directories.append(name)
            directory_names[:] = retained_directories
            for name in file_names:
                candidate = current / name
                if candidate.suffix not in valid_extensions:
                    continue
                discovered.append(
                    self.validate_project_file(candidate, canonical_project_root)
                )
        return discovered

    @staticmethod
    def _absolute_lexical(supplied: str | Path, relative_base: Path | None) -> Path:
        raw = Path(supplied).expanduser()
        if ".." in raw.parts:
            raise SecurityViolation("path_traversal_not_allowed")
        if not raw.is_absolute():
            if relative_base is None:
                raise SecurityViolation("absolute_path_required")
            raw = relative_base / raw
        return Path(os.path.abspath(os.path.normpath(raw)))

    def _lexical_allowed_root(self, lexical: Path) -> Path:
        for root in self.allowed_roots:
            if _is_relative_to(lexical, root):
                return root
        raise SecurityViolation("path_not_allowed")

    @staticmethod
    def _reject_reparse_descendants(root: Path, lexical: Path) -> None:
        try:
            relative = lexical.relative_to(root)
        except ValueError as error:
            raise SecurityViolation("path_not_allowed") from error
        current = root
        for part in relative.parts:
            current = current / part
            if _is_reparse_point(current):
                raise SecurityViolation("link_not_allowed")


def validate_registered_project(
    policy: PathSecurityPolicy,
    stored_path: str,
    stored_owner: str,
    stored_stable_id: str | None,
) -> Path:
    """Validate a persisted project row before any destructive operation."""
    path = Path(stored_path)
    if (
        stored_owner != TRUSTED_LOCAL_OWNER
        or stored_stable_id is None
        or not path.is_absolute()
    ):
        raise SecurityViolation("project_identity_invalid")
    try:
        canonical_root = policy.validate_project_root(path)
    except SecurityViolation as error:
        raise SecurityViolation("project_identity_invalid") from error
    if stored_stable_id != stable_project_id(canonical_root):
        raise SecurityViolation("project_identity_invalid")
    return canonical_root
