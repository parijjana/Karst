from __future__ import annotations

import sqlite3
from collections import defaultdict

from src.db_migration_support import (
    SqlRow,
    SqlValue,
    as_int,
    columns,
    record_conflict,
    select_dicts,
    table_names,
)


Row = SqlRow


def _groups(
    rows: list[Row], keys: tuple[str, ...]
) -> dict[tuple[SqlValue, ...], list[Row]]:
    grouped: dict[tuple[SqlValue, ...], list[Row]] = defaultdict(list)
    for row in rows:
        key = tuple(row[item] for item in keys)
        grouped[key].append(row)
    return grouped


def _conflict_group(
    connection: sqlite3.Connection,
    table: str,
    rows: list[Row],
    reason: str,
    key: tuple[SqlValue, ...],
) -> None:
    conflict_key = repr(key)
    for row in rows:
        record_conflict(connection, table, row, reason, conflict_key)


def copy_nodes(connection: sqlite3.Connection) -> dict[int, int]:
    rows = select_dicts(
        connection,
        "SELECT id, project_id, file_id, type, name, start_line, end_line "
        "FROM nodes ORDER BY id",
    )
    grouped = _groups(
        rows,
        ("project_id", "file_id", "type", "name", "start_line", "end_line"),
    )
    node_map: dict[int, int] = {}
    for key, candidates in grouped.items():
        canonical = min(candidates, key=lambda row: as_int(row["id"]))
        canonical_id = as_int(canonical["id"])
        if len(candidates) > 1:
            _conflict_group(
                connection,
                "nodes",
                candidates,
                "duplicate_node_identity",
                key,
            )
        connection.execute(
            "INSERT INTO nodes_v2 "
            "(id, project_id, file_id, type, name, start_line, end_line) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            tuple(canonical[column] for column in (
                "id",
                "project_id",
                "file_id",
                "type",
                "name",
                "start_line",
                "end_line",
            )),
        )
        for row in candidates:
            node_map[as_int(row["id"])] = canonical_id
    return node_map


def copy_edges(connection: sqlite3.Connection, node_map: dict[int, int]) -> None:
    rows = select_dicts(
        connection,
        "SELECT id, project_id, source_id, target_id, type FROM edges ORDER BY id",
    )
    candidates: list[Row] = []
    for row in rows:
        source_id = node_map[as_int(row["source_id"])]
        target_id = node_map[as_int(row["target_id"])]
        if source_id == target_id:
            record_conflict(
                connection,
                "edges",
                row,
                "collapsed_self_loop",
                repr((row["project_id"], source_id, target_id, row["type"])),
            )
            continue
        candidate = dict(row)
        candidate["original_source_id"] = row["source_id"]
        candidate["original_target_id"] = row["target_id"]
        candidate["source_id"] = source_id
        candidate["target_id"] = target_id
        if (
            source_id != as_int(row["source_id"])
            or target_id != as_int(row["target_id"])
        ):
            record_conflict(
                connection,
                "edges",
                candidate,
                "remapped_edge_endpoint",
                repr((row["project_id"], source_id, target_id, row["type"])),
            )
        candidates.append(candidate)

    grouped = _groups(candidates, ("project_id", "source_id", "target_id", "type"))
    for key, edge_group in grouped.items():
        if len(edge_group) > 1:
            _conflict_group(
                connection,
                "edges",
                edge_group,
                "duplicate_edge_identity",
                key,
            )
        canonical = min(edge_group, key=lambda row: as_int(row["id"]))
        connection.execute(
            "INSERT INTO edges_v2 (id, project_id, source_id, target_id, type) "
            "VALUES (?, ?, ?, ?, ?)",
            tuple(
                canonical[column]
                for column in ("id", "project_id", "source_id", "target_id", "type")
            ),
        )


def copy_embeddings(connection: sqlite3.Connection, node_map: dict[int, int]) -> None:
    if "embeddings" not in table_names(connection):
        return
    embedding_columns = columns(connection, "embeddings")
    content_hash = "content_hash" if "content_hash" in embedding_columns else "NULL"
    model_revision = "model_revision" if "model_revision" in embedding_columns else "NULL"
    rows = select_dicts(
        connection,
        "SELECT id, node_id, vector, "
        f"{content_hash} AS content_hash, {model_revision} AS model_revision "
        "FROM embeddings ORDER BY id",
    )
    for row in rows:
        row["mapped_node_id"] = node_map[as_int(row["node_id"])]
    grouped = _groups(rows, ("mapped_node_id",))
    for key, embedding_group in grouped.items():
        if len(embedding_group) > 1:
            _conflict_group(
                connection,
                "embeddings",
                embedding_group,
                "conflicting_node_embedding",
                key,
            )
        elif as_int(embedding_group[0]["node_id"]) != as_int(
            embedding_group[0]["mapped_node_id"]
        ):
            record_conflict(
                connection,
                "embeddings",
                embedding_group[0],
                "remapped_node_embedding",
                repr(key),
            )
        canonical = min(
            embedding_group,
            key=lambda row: (
                as_int(row["node_id"]) != as_int(row["mapped_node_id"]),
                as_int(row["id"]),
            ),
        )
        connection.execute(
            "INSERT INTO embeddings_v2 "
            "(id, node_id, vector, content_hash, model_revision) VALUES (?, ?, ?, ?, ?)",
            (
                canonical["id"],
                canonical["mapped_node_id"],
                canonical["vector"],
                canonical["content_hash"],
                canonical["model_revision"],
            ),
        )


def copy_commits(connection: sqlite3.Connection) -> dict[int, int]:
    rows = select_dicts(
        connection,
        "SELECT id, project_id, commit_hash, message, timestamp FROM commits ORDER BY id",
    )
    grouped = _groups(rows, ("project_id", "commit_hash"))
    commit_map: dict[int, int] = {}
    for key, candidates in grouped.items():
        if len(candidates) > 1:
            _conflict_group(
                connection,
                "commits",
                candidates,
                "duplicate_commit_identity",
                key,
            )
        canonical = min(candidates, key=lambda row: as_int(row["id"]))
        canonical_id = as_int(canonical["id"])
        connection.execute(
            "INSERT INTO commits_v2 "
            "(id, project_id, commit_hash, message, timestamp) VALUES (?, ?, ?, ?, ?)",
            tuple(
                canonical[column]
                for column in ("id", "project_id", "commit_hash", "message", "timestamp")
            ),
        )
        for row in candidates:
            commit_map[as_int(row["id"])] = canonical_id
    return commit_map


def copy_commit_files(
    connection: sqlite3.Connection, commit_map: dict[int, int]
) -> None:
    rows = select_dicts(
        connection,
        "SELECT id, commit_id, file_path, status FROM commit_files ORDER BY id",
    )
    for row in rows:
        row["mapped_commit_id"] = commit_map[as_int(row["commit_id"])]
    grouped = _groups(rows, ("mapped_commit_id", "file_path"))
    for key, changed_group in grouped.items():
        if len(changed_group) > 1:
            _conflict_group(
                connection,
                "commit_files",
                changed_group,
                "conflicting_commit_file",
                key,
            )
        elif as_int(changed_group[0]["commit_id"]) != as_int(
            changed_group[0]["mapped_commit_id"]
        ):
            record_conflict(
                connection,
                "commit_files",
                changed_group[0],
                "remapped_commit_file",
                repr(key),
            )
        canonical = min(changed_group, key=lambda row: as_int(row["id"]))
        connection.execute(
            "INSERT INTO commit_files_v2 (id, commit_id, file_path, status) "
            "VALUES (?, ?, ?, ?)",
            (
                canonical["id"],
                canonical["mapped_commit_id"],
                canonical["file_path"],
                canonical["status"],
            ),
        )
