from __future__ import annotations

import sqlite3
from collections import Counter

from src.db_migration_support import table_names
from src.db_schema import MANAGED_TABLE_SQL


class SchemaShapeError(RuntimeError):
    """Raised when a current-version database does not match its contract."""


EXPECTED_COLUMNS = {
    "projects": ("id", "name", "path", "owner", "stable_id"),
    "files": ("id", "project_id", "path", "hash"),
    "nodes": tuple("id project_id file_id type name start_line end_line".split()),
    "edges": ("id", "project_id", "source_id", "target_id", "type"),
    "telemetry": tuple(
        "id project_id tool_name latency_ms tokens_saved details timestamp".split()
    ),
    "commits": ("id", "project_id", "commit_hash", "message", "timestamp"),
    "commit_files": ("id", "commit_id", "file_path", "status"),
    "active_processes": ("pid", "script_name", "last_heartbeat", "last_status"),
    "embeddings": ("id", "node_id", "vector", "content_hash", "model_revision"),
    "migration_conflicts": tuple(
        "id migration_version table_name row_id reason conflict_key payload_json "
        "recorded_at".split()
    ),
    "migration_audit": tuple(
        "migration_version conflict_count details_json applied_at".split()
    ),
    "schema_migrations": ("version", "name", "checksum", "applied_at"),
}


EXPECTED_NOT_NULL = {
    "projects": {"name", "path"},
    "files": {"project_id", "path", "hash"},
    "nodes": {"project_id", "file_id", "type", "name", "start_line", "end_line"},
    "edges": {"project_id", "source_id", "target_id", "type"},
    "telemetry": {"tool_name", "latency_ms", "tokens_saved", "timestamp"},
    "commits": {"project_id", "commit_hash", "message", "timestamp"},
    "commit_files": {"commit_id", "file_path", "status"},
    "active_processes": {"script_name", "last_heartbeat", "last_status"},
    "embeddings": {"node_id", "vector"},
    "migration_conflicts": {
        "migration_version",
        "table_name",
        "row_id",
        "reason",
        "conflict_key",
        "payload_json",
        "recorded_at",
    },
    "migration_audit": {"conflict_count", "details_json", "applied_at"},
    "schema_migrations": {"name", "checksum", "applied_at"},
}


EXPECTED_PRIMARY_KEYS = {
    "projects": "id",
    "files": "id",
    "nodes": "id",
    "edges": "id",
    "telemetry": "id",
    "commits": "id",
    "commit_files": "id",
    "active_processes": "pid",
    "embeddings": "id",
    "migration_conflicts": "id",
    "migration_audit": "migration_version",
    "schema_migrations": "version",
}


EXPECTED_DEFAULTS = {
    "telemetry": {"tokens_saved": "0", "timestamp": "CURRENT_TIMESTAMP"},
    "commits": {"message": "''", "timestamp": "CURRENT_TIMESTAMP"},
    "active_processes": {"last_heartbeat": "CURRENT_TIMESTAMP"},
    "migration_conflicts": {"recorded_at": "CURRENT_TIMESTAMP"},
    "migration_audit": {"applied_at": "CURRENT_TIMESTAMP"},
    "schema_migrations": {"applied_at": "CURRENT_TIMESTAMP"},
}


EXPECTED_INDEXES = {
    "ux_projects_name": ("projects", True, ("name",)),
    "ux_projects_stable_id": ("projects", True, ("stable_id",)),
    "ux_files_project_id": ("files", True, ("project_id", "id")),
    "ux_files_project_path": ("files", True, ("project_id", "path")),
    "ux_nodes_project_id": ("nodes", True, ("project_id", "id")),
    "ux_nodes_identity": (
        "nodes",
        True,
        ("file_id", "type", "name", "start_line", "end_line"),
    ),
    "ix_nodes_project_name": ("nodes", False, ("project_id", "name")),
    "ux_edges_identity": (
        "edges",
        True,
        ("project_id", "source_id", "target_id", "type"),
    ),
    "ix_edges_source": ("edges", False, ("source_id",)),
    "ix_edges_target": ("edges", False, ("target_id",)),
    "ix_telemetry_timestamp": ("telemetry", False, ("timestamp",)),
    "ix_telemetry_project_timestamp": (
        "telemetry",
        False,
        ("project_id", "timestamp"),
    ),
    "ux_commits_project_hash": (
        "commits",
        True,
        ("project_id", "commit_hash"),
    ),
    "ix_commits_project_timestamp": (
        "commits",
        False,
        ("project_id", "timestamp"),
    ),
    "ux_commit_files_identity": (
        "commit_files",
        True,
        ("commit_id", "file_path"),
    ),
    "ux_embeddings_node": ("embeddings", True, ("node_id",)),
    "ix_active_processes_heartbeat": (
        "active_processes",
        False,
        ("last_heartbeat",),
    ),
}


EXPECTED_FOREIGN_KEYS = {
    "files": Counter({(("projects", "project_id", "id", "CASCADE"),): 1}),
    "nodes": Counter(
        {
            (
                ("files", "project_id", "project_id", "CASCADE"),
                ("files", "file_id", "id", "CASCADE"),
            ): 1,
        }
    ),
    "edges": Counter(
        {
            (
                ("nodes", "project_id", "project_id", "CASCADE"),
                ("nodes", "source_id", "id", "CASCADE"),
            ): 1,
            (
                ("nodes", "project_id", "project_id", "CASCADE"),
                ("nodes", "target_id", "id", "CASCADE"),
            ): 1,
        }
    ),
    "telemetry": Counter({(("projects", "project_id", "id", "CASCADE"),): 1}),
    "commits": Counter({(("projects", "project_id", "id", "CASCADE"),): 1}),
    "commit_files": Counter({(("commits", "commit_id", "id", "CASCADE"),): 1}),
    "embeddings": Counter({(("nodes", "node_id", "id", "CASCADE"),): 1}),
}


ForeignKeyColumn = tuple[str, str, str, str]
ForeignKeyGroup = tuple[ForeignKeyColumn, ...]


def _actual_foreign_keys(
    connection: sqlite3.Connection, table: str
) -> Counter[ForeignKeyGroup]:
    grouped: dict[int, list[tuple[int, ForeignKeyColumn]]] = {}
    for row in connection.execute(f"PRAGMA foreign_key_list({table})"):
        group_id = int(row[0])
        sequence = int(row[1])
        column = (str(row[2]), str(row[3]), str(row[4]), str(row[6]).upper())
        grouped.setdefault(group_id, []).append((sequence, column))
    return Counter(
        tuple(column for _sequence, column in sorted(group))
        for group in grouped.values()
    )


def _normalize_schema_sql(sql: str) -> str:
    return "".join(
        character
        for character in sql.upper()
        if not character.isspace() and character not in {'"', "`", "[", "]"}
    )


def _normalize_default_sql(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    while normalized.startswith("(") and normalized.endswith(")"):
        candidate = normalized[1:-1].strip()
        if candidate.count("(") != candidate.count(")"):
            break
        normalized = candidate
    return normalized


EXPECTED_STABLE_ID_INDEX_SQL = _normalize_schema_sql(
    "CREATE UNIQUE INDEX ux_projects_stable_id ON projects(stable_id) "
    "WHERE stable_id IS NOT NULL"
)


def validate_schema_shape(
    connection: sqlite3.Connection,
    *,
    excluded_tables: frozenset[str] = frozenset(),
) -> None:
    available = table_names(connection)
    for table, expected_table_columns in EXPECTED_COLUMNS.items():
        if table in excluded_tables:
            continue
        if table not in available:
            raise SchemaShapeError(f"Current schema shape is missing table {table}.")
        table_info = connection.execute(f"PRAGMA table_info({table})").fetchall()
        actual = tuple(str(row[1]) for row in table_info)
        if actual != expected_table_columns:
            raise SchemaShapeError(f"Current schema shape for {table} is invalid.")
        actual_not_null = {str(row[1]) for row in table_info if bool(row[3])}
        if actual_not_null != EXPECTED_NOT_NULL[table]:
            raise SchemaShapeError(
                f"Current schema shape nullability for {table} is invalid."
            )
        primary_keys = [str(row[1]) for row in table_info if bool(row[5])]
        if primary_keys != [EXPECTED_PRIMARY_KEYS[table]]:
            raise SchemaShapeError(
                f"Current schema shape primary key for {table} is invalid."
            )
        actual_defaults = {str(row[1]): row[4] for row in table_info}
        for column, expected_default in EXPECTED_DEFAULTS.get(table, {}).items():
            if _normalize_default_sql(actual_defaults[column]) != (
                _normalize_default_sql(expected_default)
            ):
                raise SchemaShapeError(
                    f"Current schema default for {table}.{column} is invalid."
                )
    for name, (table, unique, expected_columns) in EXPECTED_INDEXES.items():
        if table in excluded_tables:
            continue
        indexes = {
            str(row[1]): (bool(row[2]), bool(row[4]))
            for row in connection.execute(f"PRAGMA index_list({table})")
        }
        expected_partial = name == "ux_projects_stable_id"
        if indexes.get(name) != (unique, expected_partial):
            raise SchemaShapeError(f"Current schema shape is missing index {name}.")
        actual_columns = tuple(
            str(row[2]) for row in connection.execute(f"PRAGMA index_info({name})")
        )
        if actual_columns != expected_columns:
            raise SchemaShapeError(f"Current schema shape for index {name} is invalid.")
        if name == "ux_projects_stable_id":
            definition = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                (name,),
            ).fetchone()
            actual_sql = "" if definition is None else str(definition[0] or "")
            if _normalize_schema_sql(actual_sql) != EXPECTED_STABLE_ID_INDEX_SQL:
                raise SchemaShapeError(
                    "Current schema partial-index predicate for stable IDs is invalid."
                )

    for table, expected_foreign_keys in EXPECTED_FOREIGN_KEYS.items():
        if table in excluded_tables:
            continue
        if _actual_foreign_keys(connection, table) != expected_foreign_keys:
            raise SchemaShapeError(
                f"Current schema shape for {table} foreign keys is invalid."
            )

    for table, expected_sql in MANAGED_TABLE_SQL.items():
        if table in excluded_tables:
            continue
        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        actual_sql = "" if definition is None else str(definition[0] or "")
        if _normalize_schema_sql(actual_sql) != _normalize_schema_sql(expected_sql):
            raise SchemaShapeError(
                f"Current schema table definition for {table} is invalid."
            )
