"""Read-model persistence for Mission Control project summaries."""
from __future__ import annotations

import sqlite3

from src.karst_core.database.db_schema_contract import (
    SchemaShapeError,
    _normalize_schema_sql,
)


SUMMARY_SCHEMA_SQL = """
ALTER TABLE files ADD COLUMN nonblank_lines INTEGER NOT NULL DEFAULT 0
    CHECK(nonblank_lines >= 0);
CREATE TABLE untracked_paths (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    generation_id INTEGER NOT NULL,
    relative_path TEXT NOT NULL CHECK(length(relative_path) > 0),
    kind TEXT NOT NULL CHECK(kind IN ('file', 'folder')),
    FOREIGN KEY(project_id, generation_id)
        REFERENCES index_generations(project_id, id) ON DELETE CASCADE,
    UNIQUE(project_id, generation_id, relative_path)
);
CREATE INDEX ix_untracked_paths_generation_kind
    ON untracked_paths(project_id, generation_id, kind, relative_path, id);
"""


def summary_schema(connection: sqlite3.Connection) -> None:
    """Install additive summary data; existing generations remain readable."""
    for statement in SUMMARY_SCHEMA_SQL.split(";"):
        statement = statement.strip()
        if statement:
            connection.execute(statement)


def validate_summary_schema_shape(connection: sqlite3.Connection) -> None:
    """Verify the additive tables/columns that back the summary contract."""
    files = tuple(row[1] for row in connection.execute("PRAGMA table_info(files)"))
    if files[-1:] != ("nonblank_lines",):
        raise SchemaShapeError("Current schema summary file columns are invalid.")
    columns = tuple(
        row[1] for row in connection.execute("PRAGMA table_info(untracked_paths)")
    )
    if columns != ("id", "project_id", "generation_id", "relative_path", "kind"):
        raise SchemaShapeError("Current schema summary inventory columns are invalid.")
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='untracked_paths'"
    ).fetchone()
    actual = "" if row is None else str(row[0] or "")
    expected = "CREATE TABLE untracked_paths (" + SUMMARY_SCHEMA_SQL.split(
        "CREATE TABLE untracked_paths (", 1
    )[1].split(";", 1)[0]
    if _normalize_schema_sql(actual) != _normalize_schema_sql(expected):
        raise SchemaShapeError("Current schema summary inventory definition is invalid.")
    index = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='ix_untracked_paths_generation_kind'"
    ).fetchone()
    if index is None:
        raise SchemaShapeError("Current schema summary inventory index is missing.")
