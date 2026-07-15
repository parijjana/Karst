from __future__ import annotations

import sqlite3
from collections import defaultdict

from src.karst_core.database.db_generation_identity import (
    LegacyFileIdentity,
    derive_legacy_file_identity,
    derive_legacy_symbol_id,
)
from src.karst_core.database.db_migration_support import SqlRow, SqlValue, as_int, execute_ddl, select_dicts
from src.karst_core.database.db_schema_contract import validate_schema_shape
from src.karst_core.database.db_schema_v3 import (
    DROP_V2_GRAPH_TABLES,
    GENERATION_SCHEMA_SQL,
    RENAME_V3_TABLES,
    V3_INDEX_SQL,
)


def generation_schema(connection: sqlite3.Connection) -> None:
    """Rebuild the v2 graph into one non-query-ready active generation."""
    validate_schema_shape(connection)
    execute_ddl(connection, GENERATION_SCHEMA_SQL)
    generation_ids = _bootstrap_generations(connection)
    file_identities = _copy_files(connection, generation_ids)
    _copy_nodes(connection, generation_ids, file_identities)
    _copy_edges(connection, generation_ids)
    _copy_embeddings(connection, generation_ids)
    for table in DROP_V2_GRAPH_TABLES:
        connection.execute(f"DROP TABLE {table}")
    for table in RENAME_V3_TABLES:
        connection.execute(f"ALTER TABLE {table}_v3 RENAME TO {table}")
    for statement in V3_INDEX_SQL:
        connection.execute(statement)


def _bootstrap_generations(connection: sqlite3.Connection) -> dict[int, int]:
    generations: dict[int, int] = {}
    projects = select_dicts(
        connection,
        "SELECT id FROM projects ORDER BY id",
    )
    for project in projects:
        project_id = as_int(project["id"])
        counts = _legacy_counts(connection, project_id)
        cursor = connection.execute(
            "INSERT INTO index_generations_v3 "
            "(project_id, ordinal, status, completed_at, promoted_at, query_ready, "
            "discovered_files, indexed_files, symbol_count, edge_count) "
            "VALUES (?, 1, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0, "
            "?, ?, ?, ?)",
            (project_id, counts[0], counts[0], counts[1], counts[2]),
        )
        generations[project_id] = int(cursor.lastrowid or 0)
    return generations


def _legacy_counts(
    connection: sqlite3.Connection, project_id: int
) -> tuple[int, int, int]:
    def count(table: str) -> int:
        return int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
        )

    return count("files"), count("nodes"), count("edges")


def _copy_files(
    connection: sqlite3.Connection,
    generation_ids: dict[int, int],
) -> dict[int, LegacyFileIdentity]:
    identities: dict[int, LegacyFileIdentity] = {}
    rows = select_dicts(
        connection,
        "SELECT file.id, file.project_id, file.path, file.hash, "
        "project.name AS project_name, project.path AS project_path, "
        "project.stable_id AS project_stable_id "
        "FROM files AS file JOIN projects AS project ON project.id = file.project_id "
        "ORDER BY file.id",
    )
    for row in rows:
        file_id = as_int(row["id"])
        project_id = as_int(row["project_id"])
        identity = derive_legacy_file_identity(
            project_id,
            str(row["project_name"]),
            str(row["project_path"]),
            None if row["project_stable_id"] is None else str(row["project_stable_id"]),
            str(row["path"]),
        )
        identities[file_id] = identity
        connection.execute(
            "INSERT INTO files_v3 "
            "(id, project_id, generation_id, stable_id, path, relative_path, "
            "identity_path, hash, byte_size) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (
                file_id,
                project_id,
                generation_ids[project_id],
                identity.stable_id,
                row["path"],
                identity.relative_path,
                identity.relative_path,
                row["hash"],
            ),
        )
    return identities


def _copy_nodes(
    connection: sqlite3.Connection,
    generation_ids: dict[int, int],
    file_identities: dict[int, LegacyFileIdentity],
) -> None:
    rows = select_dicts(
        connection,
        "SELECT id, project_id, file_id, type, name, start_line, end_line "
        "FROM nodes ORDER BY id",
    )
    grouped: dict[tuple[SqlValue, ...], list[SqlRow]] = defaultdict(list)
    for row in rows:
        file_identity = file_identities[as_int(row["file_id"])]
        key = (
            row["project_id"],
            row["file_id"],
            file_identity.language,
            row["type"],
            row["name"],
        )
        grouped[key].append(row)
    for candidates in grouped.values():
        duplicate = len(candidates) > 1
        for row in candidates:
            node_id = as_int(row["id"])
            project_id = as_int(row["project_id"])
            file_id = as_int(row["file_id"])
            file_identity = file_identities[file_id]
            overload = f"legacy:{node_id}" if duplicate else None
            stable_id = derive_legacy_symbol_id(
                file_identity.stable_id,
                file_identity.language,
                str(row["type"]),
                str(row["name"]),
                overload,
            )
            connection.execute(
                "INSERT INTO nodes_v3 "
                "(id, project_id, generation_id, file_id, stable_id, language, type, "
                "name, qualified_name, signature, overload_discriminator, start_line, "
                "end_line) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                (
                    node_id,
                    project_id,
                    generation_ids[project_id],
                    file_id,
                    stable_id,
                    file_identity.language,
                    row["type"],
                    row["name"],
                    row["name"],
                    overload,
                    row["start_line"],
                    row["end_line"],
                ),
            )


def _copy_edges(connection: sqlite3.Connection, generation_ids: dict[int, int]) -> None:
    rows = select_dicts(
        connection,
        "SELECT id, project_id, source_id, target_id, type FROM edges ORDER BY id",
    )
    for row in rows:
        project_id = as_int(row["project_id"])
        connection.execute(
            "INSERT INTO edges_v3 "
            "(id, project_id, generation_id, source_id, target_id, type) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                project_id,
                generation_ids[project_id],
                row["source_id"],
                row["target_id"],
                row["type"],
            ),
        )


def _copy_embeddings(
    connection: sqlite3.Connection, generation_ids: dict[int, int]
) -> None:
    rows = select_dicts(
        connection,
        "SELECT embedding.id, node.project_id, embedding.node_id, embedding.vector, "
        "embedding.content_hash, embedding.model_revision "
        "FROM embeddings AS embedding JOIN nodes AS node ON node.id = embedding.node_id "
        "ORDER BY embedding.id",
    )
    for row in rows:
        project_id = as_int(row["project_id"])
        connection.execute(
            "INSERT INTO embeddings_v3 "
            "(id, project_id, generation_id, node_id, vector, content_hash, "
            "model_revision) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                project_id,
                generation_ids[project_id],
                row["node_id"],
                row["vector"],
                row["content_hash"],
                row["model_revision"],
            ),
        )
