from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from src.karst_core.database.database import Database
from src.settings import TRUSTED_LOCAL_OWNER


PROJECT_ID = str(uuid5(NAMESPACE_URL, "project:/first"))
SECOND_PROJECT_ID = str(uuid5(NAMESPACE_URL, "project:/second"))
MANIFEST = "a" * 64


EXPECTED_COLUMNS = {
    "index_generations": (
        "id",
        "project_id",
        "ordinal",
        "status",
        "created_at",
        "completed_at",
        "promoted_at",
        "superseded_at",
        "manifest_sha256",
        "failure_code",
        "query_ready",
        "discovered_files",
        "indexed_files",
        "unchanged_files",
        "skipped_files",
        "deleted_files",
        "renamed_files",
        "failed_files",
        "symbol_count",
        "edge_count",
        "diagnostic_count",
    ),
    "index_diagnostics": (
        "id",
        "project_id",
        "generation_id",
        "relative_path",
        "severity",
        "code",
        "message",
        "exception_type",
        "created_at",
    ),
    "files": (
        "id",
        "project_id",
        "generation_id",
        "stable_id",
        "path",
        "relative_path",
        "identity_path",
        "hash",
        "byte_size",
    ),
    "nodes": (
        "id",
        "project_id",
        "generation_id",
        "file_id",
        "stable_id",
        "language",
        "type",
        "name",
        "qualified_name",
        "signature",
        "overload_discriminator",
        "start_line",
        "end_line",
    ),
    "edges": (
        "id",
        "project_id",
        "generation_id",
        "source_id",
        "target_id",
        "type",
    ),
    "embeddings": (
        "id",
        "project_id",
        "generation_id",
        "node_id",
        "vector",
        "content_hash",
        "model_revision",
    ),
}


def add_project(database: Database, name: str, stable_id: str) -> int:
    return database.add_project(
        name,
        f"/{name}",
        TRUSTED_LOCAL_OWNER,
        stable_id,
    )


def active_generation(database: Database, project_id: int) -> int:
    return int(
        database.conn.execute(
            "SELECT id FROM index_generations "
            "WHERE project_id = ? AND status = 'active'",
            (project_id,),
        ).fetchone()[0]
    )


def insert_staging(
    database: Database,
    project_id: int,
    ordinal: int = 2,
) -> int:
    cursor = database.conn.execute(
        "INSERT INTO index_generations (project_id, ordinal, status) "
        "VALUES (?, ?, 'staging')",
        (project_id, ordinal),
    )
    return int(cursor.lastrowid or 0)
