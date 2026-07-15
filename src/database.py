from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from src.db_migrations import MigrationError, migrate
from src.db_graph_repository import IntegrityReport as IntegrityReport
from src.db_integrity_repository import IntegrityRepositoryMixin


class DatabaseMigrationRecoveryRequired(RuntimeError):
    """Raised when a legacy database needs an explicitly requested rebuild."""


class Database(IntegrityRepositoryMixin):
    """Thread-affine SQLite repository with explicit managed transactions."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._closed = False
        self._owner_thread_id = threading.get_ident()
        self._transaction_depth = 0
        self._savepoint_sequence = 0
        self.conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            isolation_level=None,
        )
        try:
            self.conn.row_factory = sqlite3.Row
            self.init_db()
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA synchronous = NORMAL")
            self.conn.execute("PRAGMA busy_timeout = 30000")
            self.conn.execute("PRAGMA foreign_keys = ON")
        except MigrationError as error:
            self.conn.close()
            self._closed = True
            if "Project and file paths must be absolute." not in str(error):
                raise
            raise DatabaseMigrationRecoveryRequired(
                "Karst cannot open this legacy database because migration 3 requires "
                "absolute project and file paths. No data was deleted. For this "
                "greenfield recovery, explicitly call "
                "rebuild_database(confirmation='DELETE_AND_REBUILD') to delete and "
                "recreate the current Karst database."
            ) from error
        except BaseException:
            self.conn.close()
            self._closed = True
            raise

    @classmethod
    def rebuild_blocked_legacy_database(cls, db_path: str | Path) -> Database:
        """Rebuild only a database blocked by the approved legacy-path recovery.

        This method is intentionally never called while opening an ordinary database.
        It is only for the specifically approved greenfield recovery workflow; it
        refuses to delete databases that are otherwise openable.
        """
        if str(db_path) == ":memory:":
            raise ValueError("An in-memory database cannot be rebuilt.")
        path = Path(db_path)
        try:
            database = cls(path)
        except DatabaseMigrationRecoveryRequired:
            pass
        else:
            database.close()
            raise ValueError(
                "Rebuild is only available for the legacy relative-path migration "
                "blocker. This database does not require that recovery."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{path}{suffix}")
            if candidate.exists():
                candidate.unlink()
        return cls(path)

    @property
    def schema_version(self) -> int:
        self._ensure_open()
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    @property
    def closed(self) -> bool:
        return self._closed

    def init_db(self) -> None:
        self._ensure_open()
        migrate(self.conn)

    def __enter__(self) -> Database:
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._ensure_owner()
        if self.conn.in_transaction:
            self.conn.rollback()
        self._transaction_depth = 0
        self.conn.close()
        self._closed = True
