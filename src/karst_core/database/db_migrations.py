from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from src.karst_core.database.db_migration_steps import baseline, harden_schema
from src.karst_core.database.db_migration_v3 import generation_schema
from src.karst_core.database.db_migration_v4 import (
    summary_schema,
    validate_summary_schema_shape,
)
from src.karst_core.database.db_migration_support import SchemaUpgradeError, table_names
from src.karst_core.database.db_schema import SCHEMA_MIGRATIONS_SQL
from src.karst_core.database.db_schema_contract import SchemaShapeError
from src.karst_core.database.db_schema_v3_contract import validate_v3_schema_shape


class MigrationError(RuntimeError):
    """Raised when the on-disk schema cannot be upgraded or trusted safely."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]
    definition: str = ""

    @property
    def checksum(self) -> str:
        material = f"{self.version}\0{self.name}\0{self.definition}".encode()
        return hashlib.sha256(material).hexdigest()


MIGRATIONS = (
    Migration(
        1,
        "establish legacy baseline",
        baseline,
        "karst-legacy-tables-v1",
    ),
    Migration(
        2,
        "harden identities and indexes",
        harden_schema,
        "karst-hardened-schema-v2-conflict-audit-composite-fks",
    ),
    Migration(
        3,
        "add generation-scoped graph",
        generation_schema,
        "karst-generation-schema-v3-lossless-legacy-text-query-readiness-identity-path",
    ),
    Migration(
        4,
        "add mission control summary data",
        summary_schema,
        "karst-summary-nonblank-loc-untracked-path-inventory-v4",
    ),
)
CURRENT_SCHEMA_VERSION = MIGRATIONS[-1].version


def _validate_plan(migrations: Sequence[Migration]) -> None:
    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(migrations) + 1)):
        raise MigrationError(
            "Migration versions must be ordered and contiguous from 1."
        )
    if any(not migration.name.strip() for migration in migrations):
        raise MigrationError("Migration names must be non-empty.")


def _validate_ledger(
    connection: sqlite3.Connection,
    current: int,
    migrations: Sequence[Migration],
) -> None:
    has_ledger = "schema_migrations" in table_names(connection)
    if current == 0:
        if (
            has_ledger
            and connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[
                0
            ]
        ):
            raise MigrationError("Migration ledger disagrees with schema version 0.")
        return
    if not has_ledger:
        raise MigrationError("Current schema is missing its migration ledger.")
    ledger_columns = tuple(
        str(row[1])
        for row in connection.execute("PRAGMA table_info(schema_migrations)")
    )
    if ledger_columns != ("version", "name", "checksum", "applied_at"):
        raise MigrationError("Migration ledger shape is invalid.")
    rows = connection.execute(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    expected = migrations[:current]
    if len(rows) != len(expected):
        raise MigrationError("Migration ledger has missing or extra entries.")
    for row, migration in zip(rows, expected, strict=True):
        if int(row[0]) != migration.version or str(row[1]) != migration.name:
            raise MigrationError("Migration ledger identity is invalid.")
        if str(row[2]) != migration.checksum:
            raise MigrationError(
                f"Migration ledger checksum mismatch at version {migration.version}."
            )


def _record_migration(connection: sqlite3.Connection, migration: Migration) -> None:
    connection.execute(
        SCHEMA_MIGRATIONS_SQL.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)
    )
    connection.execute(
        "INSERT INTO schema_migrations (version, name, checksum) VALUES (?, ?, ?)",
        (migration.version, migration.name, migration.checksum),
    )
    connection.execute(f"PRAGMA user_version = {migration.version}")


def _validate_integrity(connection: sqlite3.Connection) -> None:
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise MigrationError("Migrated schema contains foreign-key violations.")
    result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    if result != "ok":
        raise MigrationError(f"Migrated schema integrity check failed: {result}.")


def migrate(
    connection: sqlite3.Connection,
    *,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> int:
    """Initialize under one write lock and atomically install every pending version."""
    _validate_plan(migrations)
    if connection.in_transaction:
        raise MigrationError("Migrations require an idle database connection.")
    target = migrations[-1].version if migrations else 0
    active: Migration | None = None
    try:
        connection.execute("BEGIN IMMEDIATE")
        current = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current > target:
            raise MigrationError(
                f"Database schema {current} is newer than this Karst build ({target})."
            )
        _validate_ledger(connection, current, migrations)
        for active in migrations[current:]:
            active.apply(connection)
            _record_migration(connection, active)
        _validate_ledger(connection, target, migrations)
        if migrations is MIGRATIONS:
            validate_v3_schema_shape(
                connection, summary_extension=target >= 4
            )
            if target >= 4:
                validate_summary_schema_shape(connection)
        _validate_integrity(connection)
        connection.commit()
    except Exception as error:
        connection.rollback()
        if isinstance(error, MigrationError):
            raise
        if isinstance(error, (SchemaUpgradeError, SchemaShapeError)):
            context = (
                f"Migration {active.version} ({active.name})" if active else "Schema"
            )
            raise MigrationError(f"{context} failed: {error}") from error
        context = (
            f"Migration {active.version} ({active.name})" if active else "Migration"
        )
        raise MigrationError(f"{context} failed: {error}") from error
    return target
