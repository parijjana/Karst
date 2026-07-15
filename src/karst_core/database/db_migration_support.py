from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import TypeAlias


SqlValue: TypeAlias = str | bytes | int | float | None
SqlRow: TypeAlias = dict[str, SqlValue]


class SchemaUpgradeError(RuntimeError):
    """Raised when legacy data cannot be migrated without guessing."""


def table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def execute_ddl(connection: sqlite3.Connection, script: str) -> None:
    """Execute simple migration DDL without sqlite3.executescript auto-commits."""
    for statement in script.split(";"):
        if normalized := statement.strip():
            connection.execute(normalized)


def select_dicts(
    connection: sqlite3.Connection,
    sql: str,
    parameters: Sequence[SqlValue] = (),
) -> list[SqlRow]:
    cursor = connection.execute(sql, parameters)
    names = [str(item[0]) for item in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def as_int(value: SqlValue) -> int:
    if value is None:
        raise SchemaUpgradeError("Required integer migration value is null.")
    return int(value)


def record_conflict(
    connection: sqlite3.Connection,
    table_name: str,
    row: Mapping[str, SqlValue],
    reason: str,
    conflict_key: str,
) -> None:
    connection.execute(
        "INSERT INTO migration_conflicts_v2 "
        "(migration_version, table_name, row_id, reason, conflict_key, payload_json) "
        "VALUES (2, ?, ?, ?, ?, ?)",
        (
            table_name,
            as_int(row["id"]),
            reason,
            conflict_key,
            json.dumps(dict(row), sort_keys=True, default=str),
        ),
    )
