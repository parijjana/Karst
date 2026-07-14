from __future__ import annotations

import re
from types import MappingProxyType


APPLICATION_TABLES = frozenset(
    {
        "projects",
        "files",
        "nodes",
        "edges",
        "telemetry",
        "commits",
        "commit_files",
        "active_processes",
    }
)


LEGACY_BASELINE_SQL = """
CREATE TABLE projects (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE,
    path TEXT
);
CREATE TABLE files (
    id INTEGER PRIMARY KEY,
    project_id INTEGER,
    path TEXT,
    hash TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE nodes (
    id INTEGER PRIMARY KEY,
    project_id INTEGER,
    file_id INTEGER,
    type TEXT,
    name TEXT,
    start_line INTEGER,
    end_line INTEGER,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
);
CREATE TABLE edges (
    id INTEGER PRIMARY KEY,
    project_id INTEGER,
    source_id INTEGER,
    target_id INTEGER,
    type TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(source_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(target_id) REFERENCES nodes(id) ON DELETE CASCADE
);
CREATE TABLE telemetry (
    id INTEGER PRIMARY KEY,
    project_id INTEGER,
    tool_name TEXT,
    latency_ms REAL,
    tokens_saved INTEGER,
    details TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE commits (
    id INTEGER PRIMARY KEY,
    project_id INTEGER,
    commit_hash TEXT,
    message TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE commit_files (
    id INTEGER PRIMARY KEY,
    commit_id INTEGER,
    file_path TEXT,
    status TEXT,
    FOREIGN KEY(commit_id) REFERENCES commits(id) ON DELETE CASCADE
);
CREATE TABLE active_processes (
    pid INTEGER PRIMARY KEY,
    script_name TEXT,
    last_heartbeat DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_status TEXT
);
"""


HARDENED_TABLES_SQL = """
CREATE TABLE projects_v2 (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL CHECK(length(name) > 0),
    path TEXT NOT NULL CHECK(length(path) > 0),
    owner TEXT CHECK(owner IS NULL OR length(owner) > 0),
    stable_id TEXT CHECK(stable_id IS NULL OR length(stable_id) > 0)
);
CREATE TABLE files_v2 (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    path TEXT NOT NULL CHECK(length(path) > 0),
    hash TEXT NOT NULL CHECK(length(hash) > 0),
    FOREIGN KEY(project_id) REFERENCES projects_v2(id) ON DELETE CASCADE
);
CREATE TABLE nodes_v2 (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    type TEXT NOT NULL CHECK(length(type) > 0),
    name TEXT NOT NULL CHECK(length(name) > 0),
    start_line INTEGER NOT NULL CHECK(start_line >= 1),
    end_line INTEGER NOT NULL CHECK(end_line >= start_line),
    FOREIGN KEY(project_id, file_id)
        REFERENCES files_v2(project_id, id) ON DELETE CASCADE
);
CREATE TABLE edges_v2 (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    type TEXT NOT NULL CHECK(length(type) > 0),
    FOREIGN KEY(project_id, source_id)
        REFERENCES nodes_v2(project_id, id) ON DELETE CASCADE,
    FOREIGN KEY(project_id, target_id)
        REFERENCES nodes_v2(project_id, id) ON DELETE CASCADE
);
CREATE TABLE telemetry_v2 (
    id INTEGER PRIMARY KEY,
    project_id INTEGER,
    tool_name TEXT NOT NULL CHECK(length(tool_name) > 0),
    latency_ms REAL NOT NULL CHECK(latency_ms >= 0),
    tokens_saved INTEGER NOT NULL DEFAULT 0 CHECK(tokens_saved >= 0),
    details TEXT,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects_v2(id) ON DELETE CASCADE
);
CREATE TABLE commits_v2 (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    commit_hash TEXT NOT NULL CHECK(length(commit_hash) > 0),
    message TEXT NOT NULL DEFAULT '',
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects_v2(id) ON DELETE CASCADE
);
CREATE TABLE commit_files_v2 (
    id INTEGER PRIMARY KEY,
    commit_id INTEGER NOT NULL,
    file_path TEXT NOT NULL CHECK(length(file_path) > 0),
    status TEXT NOT NULL CHECK(length(status) > 0),
    FOREIGN KEY(commit_id) REFERENCES commits_v2(id) ON DELETE CASCADE
);
CREATE TABLE active_processes_v2 (
    pid INTEGER PRIMARY KEY CHECK(pid > 0),
    script_name TEXT NOT NULL CHECK(length(script_name) > 0),
    last_heartbeat DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_status TEXT NOT NULL CHECK(length(last_status) > 0)
);
CREATE TABLE embeddings_v2 (
    id INTEGER PRIMARY KEY,
    node_id INTEGER NOT NULL,
    vector TEXT NOT NULL CHECK(length(vector) > 0),
    content_hash TEXT,
    model_revision TEXT,
    FOREIGN KEY(node_id) REFERENCES nodes_v2(id) ON DELETE CASCADE
);
CREATE TABLE migration_conflicts_v2 (
    id INTEGER PRIMARY KEY,
    migration_version INTEGER NOT NULL CHECK(migration_version > 0),
    table_name TEXT NOT NULL CHECK(length(table_name) > 0),
    row_id INTEGER NOT NULL,
    reason TEXT NOT NULL CHECK(length(reason) > 0),
    conflict_key TEXT NOT NULL CHECK(length(conflict_key) > 0),
    payload_json TEXT NOT NULL CHECK(length(payload_json) > 0),
    recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE migration_audit_v2 (
    migration_version INTEGER PRIMARY KEY CHECK(migration_version > 0),
    conflict_count INTEGER NOT NULL CHECK(conflict_count >= 0),
    details_json TEXT NOT NULL CHECK(length(details_json) > 0),
    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


DROP_LEGACY_TABLES = (
    "embeddings",
    "commit_files",
    "edges",
    "nodes",
    "files",
    "commits",
    "telemetry",
    "active_processes",
    "projects",
)


RENAME_HARDENED_TABLES = (
    "projects",
    "files",
    "nodes",
    "edges",
    "telemetry",
    "commits",
    "commit_files",
    "active_processes",
    "embeddings",
    "migration_conflicts",
    "migration_audit",
)


PRECOPY_INDEX_SQL = (
    "CREATE UNIQUE INDEX ux_files_project_id ON files_v2(project_id, id)",
    "CREATE UNIQUE INDEX ux_nodes_project_id ON nodes_v2(project_id, id)",
)


INDEX_SQL = (
    "CREATE UNIQUE INDEX ux_projects_name ON projects(name)",
    "CREATE UNIQUE INDEX ux_projects_stable_id "
    "ON projects(stable_id) WHERE stable_id IS NOT NULL",
    "CREATE UNIQUE INDEX ux_files_project_path ON files(project_id, path)",
    "CREATE UNIQUE INDEX ux_nodes_identity "
    "ON nodes(file_id, type, name, start_line, end_line)",
    "CREATE INDEX ix_nodes_project_name ON nodes(project_id, name)",
    "CREATE UNIQUE INDEX ux_edges_identity "
    "ON edges(project_id, source_id, target_id, type)",
    "CREATE INDEX ix_edges_source ON edges(source_id)",
    "CREATE INDEX ix_edges_target ON edges(target_id)",
    "CREATE INDEX ix_telemetry_timestamp ON telemetry(timestamp)",
    "CREATE INDEX ix_telemetry_project_timestamp "
    "ON telemetry(project_id, timestamp DESC)",
    "CREATE UNIQUE INDEX ux_commits_project_hash ON commits(project_id, commit_hash)",
    "CREATE INDEX ix_commits_project_timestamp ON commits(project_id, timestamp DESC)",
    "CREATE UNIQUE INDEX ux_commit_files_identity "
    "ON commit_files(commit_id, file_path)",
    "CREATE UNIQUE INDEX ux_embeddings_node ON embeddings(node_id)",
    "CREATE INDEX ix_active_processes_heartbeat ON active_processes(last_heartbeat)",
)


SCHEMA_MIGRATIONS_SQL = (
    "CREATE TABLE schema_migrations ("
    "version INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
    "checksum TEXT NOT NULL, "
    "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
)


def _canonical_managed_table_sql() -> dict[str, str]:
    definitions: dict[str, str] = {}
    for raw_statement in HARDENED_TABLES_SQL.split(";"):
        statement = raw_statement.strip()
        if not statement:
            continue
        source_name = statement.split(maxsplit=3)[2]
        if not source_name.endswith("_v2"):
            raise RuntimeError("Hardened table definition has an invalid name.")
        current_name = source_name.removesuffix("_v2")
        definitions[current_name] = re.sub(
            r"\b([A-Za-z_][A-Za-z0-9_]*)_v2\b", r"\1", statement
        )
    definitions["schema_migrations"] = SCHEMA_MIGRATIONS_SQL
    return definitions


MANAGED_TABLE_SQL = MappingProxyType(_canonical_managed_table_sql())
