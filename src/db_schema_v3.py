from __future__ import annotations

import re
from types import MappingProxyType

from src.db_schema import MANAGED_TABLE_SQL


UUID5_CHECK = (
    "length(stable_id) = 36 AND stable_id = lower(stable_id) "
    "AND stable_id NOT GLOB '*[^0-9a-f-]*' "
    "AND substr(stable_id, 9, 1) = '-' AND substr(stable_id, 14, 1) = '-' "
    "AND substr(stable_id, 15, 1) = '5' AND substr(stable_id, 19, 1) = '-' "
    "AND substr(stable_id, 20, 1) IN ('8', '9', 'a', 'b') "
    "AND substr(stable_id, 24, 1) = '-'"
)

RELATIVE_PATH_CHECK = (
    "length(relative_path) > 0"
)


GENERATION_SCHEMA_SQL = f"""
CREATE TABLE index_generations_v3 (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL CHECK(ordinal > 0),
    status TEXT NOT NULL CHECK(status IN (
        'staging', 'active', 'superseded', 'failed', 'cancelled'
    )),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME,
    promoted_at DATETIME,
    superseded_at DATETIME,
    manifest_sha256 TEXT CHECK(
        manifest_sha256 IS NULL OR (
            length(manifest_sha256) = 64 AND manifest_sha256 = lower(manifest_sha256)
            AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    failure_code TEXT CHECK(
        failure_code IS NULL OR length(failure_code) > 0
    ),
    query_ready INTEGER NOT NULL DEFAULT 0 CHECK(query_ready IN (0, 1)),
    discovered_files INTEGER NOT NULL DEFAULT 0 CHECK(discovered_files >= 0),
    indexed_files INTEGER NOT NULL DEFAULT 0 CHECK(indexed_files >= 0),
    unchanged_files INTEGER NOT NULL DEFAULT 0 CHECK(unchanged_files >= 0),
    skipped_files INTEGER NOT NULL DEFAULT 0 CHECK(skipped_files >= 0),
    deleted_files INTEGER NOT NULL DEFAULT 0 CHECK(deleted_files >= 0),
    renamed_files INTEGER NOT NULL DEFAULT 0 CHECK(renamed_files >= 0),
    failed_files INTEGER NOT NULL DEFAULT 0 CHECK(failed_files >= 0),
    symbol_count INTEGER NOT NULL DEFAULT 0 CHECK(symbol_count >= 0),
    edge_count INTEGER NOT NULL DEFAULT 0 CHECK(edge_count >= 0),
    diagnostic_count INTEGER NOT NULL DEFAULT 0 CHECK(diagnostic_count >= 0),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, id),
    UNIQUE(project_id, ordinal),
    CHECK(
        (status = 'staging' AND completed_at IS NULL AND promoted_at IS NULL
            AND superseded_at IS NULL AND manifest_sha256 IS NULL
            AND failure_code IS NULL AND query_ready = 0)
        OR (status = 'active' AND completed_at IS NOT NULL
            AND promoted_at IS NOT NULL AND superseded_at IS NULL
            AND failure_code IS NULL
            AND ((query_ready = 0 AND manifest_sha256 IS NULL)
                OR (query_ready = 1 AND manifest_sha256 IS NOT NULL)))
        OR (status = 'superseded' AND completed_at IS NOT NULL
            AND promoted_at IS NOT NULL AND superseded_at IS NOT NULL
            AND failure_code IS NULL
            AND ((query_ready = 0 AND manifest_sha256 IS NULL)
                OR (query_ready = 1 AND manifest_sha256 IS NOT NULL)))
        OR (status IN ('failed', 'cancelled') AND completed_at IS NOT NULL
            AND promoted_at IS NULL AND superseded_at IS NULL
            AND manifest_sha256 IS NULL AND failure_code IS NOT NULL
            AND query_ready = 0)
    )
);
CREATE TABLE index_diagnostics_v3 (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    generation_id INTEGER NOT NULL,
    relative_path TEXT CHECK(relative_path IS NULL OR ({RELATIVE_PATH_CHECK})),
    severity TEXT NOT NULL CHECK(severity IN ('info', 'warning', 'error', 'fatal')),
    code TEXT NOT NULL CHECK(length(code) > 0),
    message TEXT NOT NULL CHECK(length(message) BETWEEN 1 AND 4096),
    exception_type TEXT CHECK(
        exception_type IS NULL OR length(exception_type) > 0
    ),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id, generation_id)
        REFERENCES index_generations_v3(project_id, id) ON DELETE CASCADE
);
CREATE TABLE files_v3 (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    generation_id INTEGER NOT NULL,
    stable_id TEXT NOT NULL CHECK({UUID5_CHECK}),
    path TEXT NOT NULL CHECK(length(path) > 0),
    relative_path TEXT NOT NULL CHECK({RELATIVE_PATH_CHECK}),
    identity_path TEXT NOT NULL CHECK({RELATIVE_PATH_CHECK}),
    hash TEXT NOT NULL CHECK(length(hash) > 0),
    byte_size INTEGER NOT NULL DEFAULT 0 CHECK(byte_size >= 0),
    FOREIGN KEY(project_id, generation_id)
        REFERENCES index_generations_v3(project_id, id) ON DELETE CASCADE,
    UNIQUE(project_id, generation_id, id)
);
CREATE TABLE nodes_v3 (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    generation_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    stable_id TEXT NOT NULL CHECK({UUID5_CHECK}),
    language TEXT NOT NULL CHECK(length(language) > 0),
    type TEXT NOT NULL CHECK(length(type) > 0),
    name TEXT NOT NULL CHECK(length(name) > 0),
    qualified_name TEXT NOT NULL CHECK(length(qualified_name) > 0),
    signature TEXT CHECK(signature IS NULL OR length(signature) > 0),
    overload_discriminator TEXT CHECK(
        overload_discriminator IS NULL
        OR length(overload_discriminator) > 0
    ),
    start_line INTEGER NOT NULL CHECK(start_line >= 1),
    end_line INTEGER NOT NULL CHECK(end_line >= start_line),
    FOREIGN KEY(project_id, generation_id, file_id)
        REFERENCES files_v3(project_id, generation_id, id) ON DELETE CASCADE,
    UNIQUE(project_id, generation_id, id)
);
CREATE TABLE edges_v3 (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    generation_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    type TEXT NOT NULL CHECK(length(type) > 0),
    FOREIGN KEY(project_id, generation_id, source_id)
        REFERENCES nodes_v3(project_id, generation_id, id) ON DELETE CASCADE,
    FOREIGN KEY(project_id, generation_id, target_id)
        REFERENCES nodes_v3(project_id, generation_id, id) ON DELETE CASCADE
);
CREATE TABLE embeddings_v3 (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    generation_id INTEGER NOT NULL,
    node_id INTEGER NOT NULL,
    vector TEXT NOT NULL CHECK(length(vector) > 0),
    content_hash TEXT,
    model_revision TEXT,
    FOREIGN KEY(project_id, generation_id, node_id)
        REFERENCES nodes_v3(project_id, generation_id, id) ON DELETE CASCADE
);
"""


DROP_V2_GRAPH_TABLES = ("embeddings", "edges", "nodes", "files")
RENAME_V3_TABLES = (
    "index_generations",
    "index_diagnostics",
    "files",
    "nodes",
    "edges",
    "embeddings",
)


V3_INDEX_SQL = (
    "CREATE UNIQUE INDEX ux_index_generations_active_project "
    "ON index_generations(project_id) WHERE status = 'active'",
    "CREATE UNIQUE INDEX ux_index_generations_staging_project "
    "ON index_generations(project_id) WHERE status = 'staging'",
    "CREATE INDEX ix_index_generations_manifest "
    "ON index_generations(project_id, manifest_sha256)",
    "CREATE INDEX ix_index_diagnostics_generation "
    "ON index_diagnostics(project_id, generation_id, id)",
    "CREATE UNIQUE INDEX ux_files_generation_stable_id "
    "ON files(project_id, generation_id, stable_id)",
    "CREATE UNIQUE INDEX ux_files_generation_relative_path "
    "ON files(project_id, generation_id, relative_path)",
    "CREATE INDEX ix_files_generation_order "
    "ON files(project_id, generation_id, relative_path, id)",
    "CREATE UNIQUE INDEX ux_nodes_generation_stable_id "
    "ON nodes(project_id, generation_id, stable_id)",
    "CREATE INDEX ix_nodes_generation_qualified_name "
    "ON nodes(project_id, generation_id, qualified_name, stable_id)",
    "CREATE INDEX ix_nodes_generation_file_order "
    "ON nodes(project_id, generation_id, file_id, start_line, qualified_name, stable_id)",
    "CREATE INDEX ix_nodes_project_name ON nodes(project_id, name, generation_id, id)",
    "CREATE UNIQUE INDEX ux_edges_generation_identity "
    "ON edges(project_id, generation_id, source_id, target_id, type)",
    "CREATE INDEX ix_edges_generation_source "
    "ON edges(project_id, generation_id, source_id, id)",
    "CREATE INDEX ix_edges_generation_target "
    "ON edges(project_id, generation_id, target_id, id)",
    "CREATE INDEX ix_edges_source ON edges(source_id)",
    "CREATE INDEX ix_edges_target ON edges(target_id)",
    "CREATE UNIQUE INDEX ux_embeddings_generation_node "
    "ON embeddings(project_id, generation_id, node_id)",
    "CREATE UNIQUE INDEX ux_embeddings_node ON embeddings(node_id)",
)


def _managed_v3_table_sql() -> dict[str, str]:
    definitions = dict(MANAGED_TABLE_SQL)
    for raw_statement in GENERATION_SCHEMA_SQL.split(";"):
        statement = raw_statement.strip()
        if not statement:
            continue
        source_name = statement.split(maxsplit=3)[2]
        current_name = source_name.removesuffix("_v3")
        definitions[current_name] = re.sub(
            r"\b([A-Za-z_][A-Za-z0-9_]*)_v3\b", r"\1", statement
        )
    return definitions


V3_MANAGED_TABLE_SQL = MappingProxyType(_managed_v3_table_sql())
