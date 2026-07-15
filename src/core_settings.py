from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRUSTED_LOCAL_OWNER = "local-stdio"


class SettingsError(ValueError):
    """Raised when Karst core configuration is not safe to use."""


def absolute_from_project(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve(strict=False)


@dataclass(frozen=True, slots=True)
class CoreSettings:
    """Configuration required by Karst's data and MCP boundaries only."""

    data_dir: Path
    db_path: Path
    allowed_roots: tuple[Path, ...]

    def __post_init__(self) -> None:
        data_dir = absolute_from_project(self.data_dir)
        db_path = absolute_from_project(self.db_path)
        canonical_roots: list[Path] = []
        for root in self.allowed_roots:
            candidate = absolute_from_project(root)
            if not candidate.is_dir():
                raise SettingsError("An allowed root is unavailable.")
            canonical = candidate.resolve(strict=True)
            if canonical not in canonical_roots:
                canonical_roots.append(canonical)
        if not canonical_roots:
            raise SettingsError("At least one allowed root is required.")
        object.__setattr__(self, "data_dir", data_dir)
        object.__setattr__(self, "db_path", db_path)
        object.__setattr__(self, "allowed_roots", tuple(canonical_roots))

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CoreSettings:
        source = os.environ if env is None else env
        if "KARST_OWNER_ID" in source:
            raise SettingsError(
                "Karst supports one trusted local stdio domain; client owners are unsupported."
            )
        data_dir = absolute_from_project(source.get("KARST_DATA_DIR", "data"))
        db_path = absolute_from_project(
            source.get("KARST_DB_PATH", str(data_dir / "knowledge_graph.db"))
        )
        raw_roots = source.get("KARST_ALLOWED_ROOTS")
        allowed_roots = (
            tuple(
                absolute_from_project(item.strip())
                for item in raw_roots.split(os.pathsep)
                if item.strip()
            )
            if raw_roots
            else (PROJECT_ROOT,)
        )
        return cls(data_dir=data_dir, db_path=db_path, allowed_roots=allowed_roots)


core_settings = CoreSettings.from_env()
