from __future__ import annotations

from collections import Counter

from src.db_schema_contract import _normalize_schema_sql


def _names(value: str) -> tuple[str, ...]:
    return tuple(value.split())


def _required(value: str) -> set[str]:
    return set(value.split())


def _index(table: str, unique: bool, columns: str) -> tuple[str, bool, tuple[str, ...]]:
    return table, unique, _names(columns)


V3_TABLES = frozenset(
    {"index_generations", "index_diagnostics", "files", "nodes", "edges", "embeddings"}
)

EXPECTED_COLUMNS = {
    "index_generations": _names(
        "id project_id ordinal status created_at completed_at promoted_at superseded_at "
        "manifest_sha256 failure_code query_ready discovered_files indexed_files "
        "unchanged_files skipped_files deleted_files renamed_files failed_files "
        "symbol_count edge_count diagnostic_count"
    ),
    "index_diagnostics": _names(
        "id project_id generation_id relative_path severity code message exception_type "
        "created_at"
    ),
    "files": _names(
        "id project_id generation_id stable_id path relative_path identity_path hash byte_size"
    ),
    "nodes": _names(
        "id project_id generation_id file_id stable_id language type name qualified_name "
        "signature overload_discriminator start_line end_line"
    ),
    "edges": _names("id project_id generation_id source_id target_id type"),
    "embeddings": _names(
        "id project_id generation_id node_id vector content_hash model_revision"
    ),
}

EXPECTED_NOT_NULL = {
    "index_generations": _required(
        "project_id ordinal status created_at query_ready discovered_files indexed_files "
        "unchanged_files skipped_files deleted_files renamed_files failed_files "
        "symbol_count edge_count diagnostic_count"
    ),
    "index_diagnostics": _required(
        "project_id generation_id severity code message created_at"
    ),
    "files": _required(
        "project_id generation_id stable_id path relative_path identity_path hash byte_size"
    ),
    "nodes": _required(
        "project_id generation_id file_id stable_id language type name qualified_name "
        "start_line end_line"
    ),
    "edges": _required("project_id generation_id source_id target_id type"),
    "embeddings": _required("project_id generation_id node_id vector"),
}

EXPECTED_DEFAULTS = {
    "index_generations": {
        "created_at": "CURRENT_TIMESTAMP",
        "query_ready": "0",
        "discovered_files": "0",
        "indexed_files": "0",
        "unchanged_files": "0",
        "skipped_files": "0",
        "deleted_files": "0",
        "renamed_files": "0",
        "failed_files": "0",
        "symbol_count": "0",
        "edge_count": "0",
        "diagnostic_count": "0",
    },
    "index_diagnostics": {"created_at": "CURRENT_TIMESTAMP"},
    "files": {"byte_size": "0"},
}

EXPECTED_INDEXES = {
    "ux_index_generations_active_project": _index(
        "index_generations", True, "project_id"
    ),
    "ux_index_generations_staging_project": _index(
        "index_generations", True, "project_id"
    ),
    "ix_index_generations_manifest": _index(
        "index_generations", False, "project_id manifest_sha256"
    ),
    "ix_index_diagnostics_generation": _index(
        "index_diagnostics", False, "project_id generation_id id"
    ),
    "ux_files_generation_stable_id": _index(
        "files", True, "project_id generation_id stable_id"
    ),
    "ux_files_generation_relative_path": _index(
        "files", True, "project_id generation_id relative_path"
    ),
    "ix_files_generation_order": _index(
        "files", False, "project_id generation_id relative_path id"
    ),
    "ux_nodes_generation_stable_id": _index(
        "nodes", True, "project_id generation_id stable_id"
    ),
    "ix_nodes_generation_qualified_name": _index(
        "nodes", False, "project_id generation_id qualified_name stable_id"
    ),
    "ix_nodes_generation_file_order": _index(
        "nodes",
        False,
        "project_id generation_id file_id start_line qualified_name stable_id",
    ),
    "ix_nodes_project_name": _index("nodes", False, "project_id name generation_id id"),
    "ux_edges_generation_identity": _index(
        "edges", True, "project_id generation_id source_id target_id type"
    ),
    "ix_edges_generation_source": _index(
        "edges", False, "project_id generation_id source_id id"
    ),
    "ix_edges_generation_target": _index(
        "edges", False, "project_id generation_id target_id id"
    ),
    "ix_edges_source": _index("edges", False, "source_id"),
    "ix_edges_target": _index("edges", False, "target_id"),
    "ux_embeddings_generation_node": _index(
        "embeddings", True, "project_id generation_id node_id"
    ),
    "ux_embeddings_node": _index("embeddings", True, "node_id"),
}

EXPECTED_FOREIGN_KEYS = {
    "index_generations": Counter({(("projects", "project_id", "id", "CASCADE"),): 1}),
    "index_diagnostics": Counter(
        {
            (
                ("index_generations", "project_id", "project_id", "CASCADE"),
                ("index_generations", "generation_id", "id", "CASCADE"),
            ): 1
        }
    ),
    "files": Counter(
        {
            (
                ("index_generations", "project_id", "project_id", "CASCADE"),
                ("index_generations", "generation_id", "id", "CASCADE"),
            ): 1
        }
    ),
    "nodes": Counter(
        {
            (
                ("files", "project_id", "project_id", "CASCADE"),
                ("files", "generation_id", "generation_id", "CASCADE"),
                ("files", "file_id", "id", "CASCADE"),
            ): 1
        }
    ),
    "edges": Counter(
        {
            (
                ("nodes", "project_id", "project_id", "CASCADE"),
                ("nodes", "generation_id", "generation_id", "CASCADE"),
                ("nodes", "source_id", "id", "CASCADE"),
            ): 1,
            (
                ("nodes", "project_id", "project_id", "CASCADE"),
                ("nodes", "generation_id", "generation_id", "CASCADE"),
                ("nodes", "target_id", "id", "CASCADE"),
            ): 1,
        }
    ),
    "embeddings": Counter(
        {
            (
                ("nodes", "project_id", "project_id", "CASCADE"),
                ("nodes", "generation_id", "generation_id", "CASCADE"),
                ("nodes", "node_id", "id", "CASCADE"),
            ): 1
        }
    ),
}

PARTIAL_INDEX_SQL = {
    "ux_index_generations_active_project": _normalize_schema_sql(
        "CREATE UNIQUE INDEX ux_index_generations_active_project "
        "ON index_generations(project_id) WHERE status = 'active'"
    ),
    "ux_index_generations_staging_project": _normalize_schema_sql(
        "CREATE UNIQUE INDEX ux_index_generations_staging_project "
        "ON index_generations(project_id) WHERE status = 'staging'"
    ),
}
