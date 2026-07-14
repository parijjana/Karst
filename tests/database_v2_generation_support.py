from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from src.db_migrations import MIGRATIONS, migrate


PROJECT_STABLE_ID = str(uuid5(NAMESPACE_URL, "project:/legacy/project"))


def create_v2_database(
    path: Path,
    *,
    project_root: str = "/legacy/project",
    file_path: str | None = "/legacy/project/src/a.py",
    project_stable_id: str | None = PROJECT_STABLE_ID,
    populated: bool = True,
) -> None:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    migrate(connection, migrations=MIGRATIONS[:2])
    connection.execute(
        "INSERT INTO projects (id, name, path, owner, stable_id) "
        "VALUES (7, 'legacy', ?, 'local-stdio', ?)",
        (project_root, project_stable_id),
    )
    connection.execute(
        "INSERT INTO projects (id, name, path, owner, stable_id) "
        "VALUES (8, 'empty', '/legacy/empty', 'local-stdio', NULL)"
    )
    if populated and file_path is not None:
        connection.execute(
            "INSERT INTO files (id, project_id, path, hash) "
            "VALUES (11, 7, ?, 'legacy-hash')",
            (file_path,),
        )
        connection.execute(
            "INSERT INTO nodes "
            "(id, project_id, file_id, type, name, start_line, end_line) "
            "VALUES (21, 7, 11, 'function', 'run', 1, 2)"
        )
        connection.execute(
            "INSERT INTO nodes "
            "(id, project_id, file_id, type, name, start_line, end_line) "
            "VALUES (22, 7, 11, 'function', 'run', 10, 12)"
        )
        connection.execute(
            "INSERT INTO edges "
            "(id, project_id, source_id, target_id, type) "
            "VALUES (31, 7, 21, 22, 'calls')"
        )
        connection.execute(
            "INSERT INTO embeddings "
            "(id, node_id, vector, content_hash, model_revision) "
            "VALUES (41, 21, '[0.1]', 'content-one', 'model@1')"
        )
        connection.execute(
            "INSERT INTO embeddings "
            "(id, node_id, vector, content_hash, model_revision) "
            "VALUES (42, 22, '[0.2]', 'content-two', 'model@1')"
        )
        connection.execute(
            "INSERT INTO commits "
            "(id, project_id, commit_hash, message, timestamp) "
            "VALUES (51, 7, 'abc', 'legacy commit', '2025-01-02 03:04:05')"
        )
        connection.execute(
            "INSERT INTO commit_files (id, commit_id, file_path, status) "
            "VALUES (52, 51, 'src/a.py', 'M')"
        )
        connection.execute(
            "INSERT INTO telemetry "
            "(id, project_id, tool_name, latency_ms, tokens_saved, details, timestamp) "
            "VALUES (61, 7, 'legacy_tool', 1.5, 9, 'kept', "
            "'2025-01-02 03:04:06')"
        )
        connection.execute(
            "INSERT INTO active_processes "
            "(pid, script_name, last_heartbeat, last_status) "
            "VALUES (71, 'legacy.py', '2025-01-02 03:04:07', 'running')"
        )
    connection.commit()
    connection.close()
