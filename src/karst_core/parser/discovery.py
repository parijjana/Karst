"""Pure, bounded source discovery and immutable snapshots."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from src.karst_core.indexing.identity import FileCandidate, SourceSnapshot
from src.security import PathSecurityPolicy, SecurityViolation, stable_project_id


@dataclass(frozen=True, slots=True)
class DiscoveryLimits:
    max_files: int = 10_000
    max_file_bytes: int = 2_000_000
    max_total_bytes: int = 100_000_000
    max_depth: int = 100
    max_update_files: int = 10_000
    deadline: float | None = None

    def __post_init__(self) -> None:
        for name in ("max_files", "max_file_bytes", "max_total_bytes", "max_depth", "max_update_files"):
            if not isinstance(getattr(self, name), int) or getattr(self, name) < 0:
                raise ValueError(f"{name} must be nonnegative")
        if self.max_file_bytes > self.max_total_bytes:
            raise ValueError("max_file_bytes exceeds max_total_bytes")


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    snapshots: tuple[SourceSnapshot, ...]
    untracked_paths: tuple[tuple[str, str], ...] = ()
    diagnostics: tuple[str, ...] = ()


def discover_snapshots(
    project_root: str | Path,
    policy: PathSecurityPolicy,
    *,
    extensions: Iterable[str] = (".dart",),
    ignored_directories: Iterable[str] = (".git", ".dart_tool", "build"),
    limits: DiscoveryLimits | None = None,
    cancelled: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> DiscoveryResult:
    """Discover sorted files and hash bytes read from one bounded open handle."""
    bound = limits or DiscoveryLimits()
    root = policy.validate_project_root(project_root)
    ext = {e if e.startswith(".") else f".{e}" for e in extensions}
    ignored = set(ignored_directories)
    paths, untracked = policy.discover_project_inventory(root, ext, ignored)
    paths = sorted(paths, key=lambda p: p.relative_to(root).as_posix())
    if len(paths) > bound.max_files or len(paths) > bound.max_update_files:
        raise SecurityViolation("index_budget_exceeded")
    project_id = stable_project_id(root)
    snapshots: list[SourceSnapshot] = []
    total = 0
    for path in paths:
        if cancelled and cancelled():
            raise SecurityViolation("index_cancelled")
        if bound.deadline is not None and clock() > bound.deadline:
            raise SecurityViolation("index_deadline_exceeded")
        relative = path.relative_to(root).as_posix()
        if len(Path(relative).parts) > bound.max_depth:
            raise SecurityViolation("index_budget_exceeded")
        candidate = FileCandidate.for_new_file(project_id, relative)
        with path.open("rb") as handle:
            content = handle.read(bound.max_file_bytes + 1)
        if len(content) > bound.max_file_bytes:
            raise SecurityViolation("index_budget_exceeded")
        total += len(content)
        if total > bound.max_total_bytes:
            raise SecurityViolation("index_budget_exceeded")
        snapshots.append(SourceSnapshot(candidate, content))
    inventory = tuple(
        (path.relative_to(root).as_posix(), kind)
        for path, kind in sorted(untracked, key=lambda entry: entry[0].relative_to(root).as_posix())
    )
    return DiscoveryResult(tuple(snapshots), inventory)


discover = discover_snapshots
