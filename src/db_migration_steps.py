from __future__ import annotations

import json
import sqlite3

from src.db_migration_conflicts import (
    copy_commit_files,
    copy_commits,
    copy_edges,
    copy_embeddings,
    copy_nodes,
)
from src.db_migration_support import (
    SchemaUpgradeError,
    columns,
    execute_ddl,
    table_names,
)
from src.db_schema import (
    APPLICATION_TABLES,
    DROP_LEGACY_TABLES,
    HARDENED_TABLES_SQL,
    INDEX_SQL,
    LEGACY_BASELINE_SQL,
    PRECOPY_INDEX_SQL,
    RENAME_HARDENED_TABLES,
)


def baseline(connection: sqlite3.Connection) -> None:
    present = table_names(connection) & APPLICATION_TABLES
    if not present:
        execute_ddl(connection, LEGACY_BASELINE_SQL)
        return
    missing = APPLICATION_TABLES - present
    if missing:
        names = ", ".join(sorted(missing))
        raise SchemaUpgradeError(f"Legacy schema is incomplete; missing tables: {names}.")


def _assert_valid_rows(connection: sqlite3.Connection) -> None:
    predicates = {
        "projects": "name IS NULL OR name = '' OR path IS NULL OR path = ''",
        "files": "project_id IS NULL OR path IS NULL OR path = '' OR hash IS NULL OR hash = ''",
        "nodes": (
            "project_id IS NULL OR file_id IS NULL OR type IS NULL OR type = '' "
            "OR name IS NULL OR name = '' OR start_line IS NULL OR start_line < 1 "
            "OR end_line IS NULL OR end_line < start_line"
        ),
        "edges": (
            "project_id IS NULL OR source_id IS NULL OR target_id IS NULL "
            "OR type IS NULL OR type = ''"
        ),
        "telemetry": (
            "tool_name IS NULL OR tool_name = '' OR latency_ms IS NULL "
            "OR latency_ms < 0 OR tokens_saved IS NULL OR tokens_saved < 0 "
            "OR timestamp IS NULL"
        ),
        "commits": (
            "project_id IS NULL OR commit_hash IS NULL OR commit_hash = '' "
            "OR message IS NULL OR timestamp IS NULL"
        ),
        "commit_files": (
            "commit_id IS NULL OR file_path IS NULL OR file_path = '' "
            "OR status IS NULL OR status = ''"
        ),
        "active_processes": (
            "pid IS NULL OR pid <= 0 OR script_name IS NULL OR script_name = '' "
            "OR last_heartbeat IS NULL OR last_status IS NULL OR last_status = ''"
        ),
    }
    if "embeddings" in table_names(connection):
        predicates["embeddings"] = "node_id IS NULL OR vector IS NULL OR vector = ''"
    for table, predicate in predicates.items():
        invalid = int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {predicate}"
            ).fetchone()[0]
        )
        if invalid:
            raise SchemaUpgradeError(
                f"Legacy table {table} contains {invalid} invalid row(s)."
            )


def _assert_unambiguous_container_identities(connection: sqlite3.Connection) -> None:
    duplicate_files = int(
        connection.execute(
            "SELECT COUNT(*) FROM (SELECT project_id, path FROM files "
            "GROUP BY project_id, path HAVING COUNT(*) > 1)"
        ).fetchone()[0]
    )
    if duplicate_files:
        raise SchemaUpgradeError(
            "Legacy schema contains duplicate file identities; migration fails closed."
        )
    project_columns = columns(connection, "projects")
    if "stable_id" in project_columns:
        duplicate_stable_ids = int(
            connection.execute(
                "SELECT COUNT(*) FROM (SELECT stable_id FROM projects "
                "WHERE stable_id IS NOT NULL GROUP BY stable_id HAVING COUNT(*) > 1)"
            ).fetchone()[0]
        )
        if duplicate_stable_ids:
            raise SchemaUpgradeError(
                "Legacy schema contains duplicate stable project identities."
            )


def _assert_legacy_project_consistency(connection: sqlite3.Connection) -> None:
    checks = {
        "orphan files": (
            "SELECT COUNT(*) FROM files AS file LEFT JOIN projects AS project "
            "ON project.id = file.project_id WHERE project.id IS NULL"
        ),
        "cross-project nodes": (
            "SELECT COUNT(*) FROM nodes AS node LEFT JOIN files AS file "
            "ON file.id = node.file_id WHERE file.id IS NULL "
            "OR file.project_id != node.project_id"
        ),
        "cross-project edges": (
            "SELECT COUNT(*) FROM edges AS edge "
            "LEFT JOIN nodes AS source ON source.id = edge.source_id "
            "LEFT JOIN nodes AS target ON target.id = edge.target_id "
            "WHERE source.id IS NULL OR target.id IS NULL "
            "OR source.project_id != edge.project_id "
            "OR target.project_id != edge.project_id"
        ),
        "orphan commits": (
            "SELECT COUNT(*) FROM commits AS item LEFT JOIN projects AS project "
            "ON project.id = item.project_id WHERE project.id IS NULL"
        ),
        "orphan commit files": (
            "SELECT COUNT(*) FROM commit_files AS item LEFT JOIN commits AS commit_row "
            "ON commit_row.id = item.commit_id WHERE commit_row.id IS NULL"
        ),
    }
    if "embeddings" in table_names(connection):
        checks["orphan embeddings"] = (
            "SELECT COUNT(*) FROM embeddings AS item LEFT JOIN nodes AS node "
            "ON node.id = item.node_id WHERE node.id IS NULL"
        )
    for label, query in checks.items():
        if int(connection.execute(query).fetchone()[0]):
            raise SchemaUpgradeError(f"Legacy schema contains {label}.")


def _copy_projects_and_files(connection: sqlite3.Connection) -> None:
    project_columns = columns(connection, "projects")
    owner = "owner" if "owner" in project_columns else "NULL"
    stable_id = "stable_id" if "stable_id" in project_columns else "NULL"
    connection.execute(
        "INSERT INTO projects_v2 (id, name, path, owner, stable_id) "
        f"SELECT id, name, path, {owner}, {stable_id} FROM projects"
    )
    connection.execute(
        "INSERT INTO files_v2 (id, project_id, path, hash) "
        "SELECT id, project_id, path, hash FROM files"
    )


def _copy_operational_tables(connection: sqlite3.Connection) -> None:
    details = "details" if "details" in columns(connection, "telemetry") else "NULL"
    connection.execute(
        "INSERT INTO telemetry_v2 "
        "(id, project_id, tool_name, latency_ms, tokens_saved, details, timestamp) "
        f"SELECT id, project_id, tool_name, latency_ms, tokens_saved, {details}, timestamp "
        "FROM telemetry"
    )
    connection.execute(
        "INSERT INTO active_processes_v2 "
        "(pid, script_name, last_heartbeat, last_status) "
        "SELECT pid, script_name, last_heartbeat, last_status FROM active_processes"
    )


def _record_migration_audit(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT table_name, reason, COUNT(*) FROM migration_conflicts_v2 "
        "GROUP BY table_name, reason ORDER BY table_name, reason"
    ).fetchall()
    details = {
        f"{table}:{reason}": int(count) for table, reason, count in rows
    }
    conflict_count = sum(details.values())
    connection.execute(
        "INSERT INTO migration_audit_v2 "
        "(migration_version, conflict_count, details_json) VALUES (2, ?, ?)",
        (conflict_count, json.dumps(details, sort_keys=True)),
    )


def harden_schema(connection: sqlite3.Connection) -> None:
    _assert_valid_rows(connection)
    _assert_unambiguous_container_identities(connection)
    _assert_legacy_project_consistency(connection)
    execute_ddl(connection, HARDENED_TABLES_SQL)
    for statement in PRECOPY_INDEX_SQL:
        connection.execute(statement)
    _copy_projects_and_files(connection)
    node_map = copy_nodes(connection)
    copy_edges(connection, node_map)
    copy_embeddings(connection, node_map)
    commit_map = copy_commits(connection)
    copy_commit_files(connection, commit_map)
    _copy_operational_tables(connection)
    _record_migration_audit(connection)
    for table in DROP_LEGACY_TABLES:
        if table in table_names(connection):
            connection.execute(f"DROP TABLE {table}")
    for table in RENAME_HARDENED_TABLES:
        connection.execute(f"ALTER TABLE {table}_v2 RENAME TO {table}")
    for statement in INDEX_SQL:
        connection.execute(statement)
