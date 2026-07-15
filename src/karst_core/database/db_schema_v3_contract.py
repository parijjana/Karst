from __future__ import annotations

import sqlite3

from src.karst_core.database.db_migration_support import table_names
from src.karst_core.database.db_schema_contract import (
    SchemaShapeError,
    _actual_foreign_keys,
    _normalize_default_sql,
    _normalize_schema_sql,
    validate_schema_shape,
)
from src.karst_core.database.db_schema_v3 import V3_MANAGED_TABLE_SQL
from src.karst_core.database.db_schema_v3_expectations import (
    EXPECTED_COLUMNS,
    EXPECTED_DEFAULTS,
    EXPECTED_FOREIGN_KEYS,
    EXPECTED_INDEXES,
    EXPECTED_NOT_NULL,
    PARTIAL_INDEX_SQL,
    V3_TABLES,
)


def validate_v3_schema_shape(connection: sqlite3.Connection) -> None:
    validate_schema_shape(connection, excluded_tables=V3_TABLES)
    available = table_names(connection)
    for table, expected_columns in EXPECTED_COLUMNS.items():
        if table not in available:
            raise SchemaShapeError(f"Current schema shape is missing table {table}.")
        table_info = connection.execute(f"PRAGMA table_info({table})").fetchall()
        if tuple(str(row[1]) for row in table_info) != expected_columns:
            raise SchemaShapeError(f"Current schema shape for {table} is invalid.")
        if {str(row[1]) for row in table_info if bool(row[3])} != EXPECTED_NOT_NULL[
            table
        ]:
            raise SchemaShapeError(
                f"Current schema nullability for {table} is invalid."
            )
        if [str(row[1]) for row in table_info if bool(row[5])] != ["id"]:
            raise SchemaShapeError(
                f"Current schema primary key for {table} is invalid."
            )
        defaults = {str(row[1]): row[4] for row in table_info}
        for column, expected_default in EXPECTED_DEFAULTS.get(table, {}).items():
            if _normalize_default_sql(defaults[column]) != _normalize_default_sql(
                expected_default
            ):
                raise SchemaShapeError(
                    f"Current schema default for {table}.{column} is invalid."
                )
    _validate_indexes(connection)
    for table, expected_foreign_keys in EXPECTED_FOREIGN_KEYS.items():
        if _actual_foreign_keys(connection, table) != expected_foreign_keys:
            raise SchemaShapeError(
                f"Current schema shape for {table} foreign keys is invalid."
            )
    for table in V3_TABLES:
        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        actual = "" if definition is None else str(definition[0] or "")
        if _normalize_schema_sql(actual) != _normalize_schema_sql(
            V3_MANAGED_TABLE_SQL[table]
        ):
            raise SchemaShapeError(
                f"Current schema table definition for {table} is invalid."
            )


def _validate_indexes(connection: sqlite3.Connection) -> None:
    for name, (table, unique, columns) in EXPECTED_INDEXES.items():
        indexes = {
            str(row[1]): (bool(row[2]), bool(row[4]))
            for row in connection.execute(f"PRAGMA index_list({table})")
        }
        partial = name in PARTIAL_INDEX_SQL
        if indexes.get(name) != (unique, partial):
            raise SchemaShapeError(f"Current schema shape is missing index {name}.")
        actual_columns = tuple(
            str(row[2]) for row in connection.execute(f"PRAGMA index_info({name})")
        )
        if actual_columns != columns:
            raise SchemaShapeError(f"Current schema shape for index {name} is invalid.")
        if partial:
            row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                (name,),
            ).fetchone()
            actual_sql = "" if row is None else str(row[0] or "")
            if _normalize_schema_sql(actual_sql) != PARTIAL_INDEX_SQL[name]:
                raise SchemaShapeError(
                    f"Current schema partial-index predicate for {name} is invalid."
                )
