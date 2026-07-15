from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from src.karst_core.database.database import Database
from src.security import stable_project_id
from src.settings import Settings
from src.settings import TRUSTED_LOCAL_OWNER


def configured_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "karst.db",
        allowed_roots=(tmp_path,),
    )


def seed_populated_legacy_generation(
    database: Database,
    project_name: str,
    project_path: Path,
    sources: dict[str, tuple[bytes, str]],
) -> int:
    project_id = database.add_project(
        project_name,
        str(project_path),
        TRUSTED_LOCAL_OWNER,
        stable_project_id(project_path),
    )
    for name, (contents, symbol_name) in sources.items():
        source = project_path / name
        source.write_bytes(contents)
        file_id = database.add_file(
            project_id,
            str(source),
            hashlib.sha256(contents).hexdigest(),
        )
        database.conn.execute(
            "UPDATE files SET byte_size = ?, stable_id = ? WHERE id = ?",
            (len(contents), str(uuid5(NAMESPACE_URL, f"legacy-{name}")), file_id),
        )
        database.add_node(project_id, file_id, "function", symbol_name, 1, 2)
    return project_id
