from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Self


class DatabaseConcurrencyError(RuntimeError):
    """Raised when a repository is used outside its owning thread."""


class DatabaseTransactionError(RuntimeError):
    """Raised when unmanaged transaction state would make a write unsafe."""


class TransactionRepositoryMixin:
    """Thread-affine explicit unit-of-work ownership for one SQLite connection."""

    conn: sqlite3.Connection
    _closed: bool
    _owner_thread_id: int
    _transaction_depth: int
    _savepoint_sequence: int

    @property
    def transaction_depth(self) -> int:
        self._ensure_open()
        return self._transaction_depth

    def _ensure_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise DatabaseConcurrencyError(
                "Database repositories are thread-affine; create one per worker thread."
            )

    def _ensure_open(self) -> None:
        self._ensure_owner()
        if self._closed:
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")

    def _before_write(self) -> None:
        self._ensure_open()
        if self.conn.in_transaction and self._transaction_depth == 0:
            raise DatabaseTransactionError(
                "Repository write rejected inside an external transaction."
            )

    def _auto_commit(self) -> None:
        """Compatibility hook; isolation_level=None commits autonomous statements."""
        self._ensure_open()

    def _commit_outer_transaction(self) -> None:
        self.conn.commit()

    def _release_savepoint(self, name: str) -> None:
        self.conn.execute(f"RELEASE SAVEPOINT {name}")

    def _rollback_outer_transaction(self) -> None:
        self.conn.rollback()

    def _rollback_savepoint(self, name: str) -> None:
        self.conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
        self.conn.execute(f"RELEASE SAVEPOINT {name}")

    @contextmanager
    def transaction(self) -> Iterator[Self]:
        """Own an atomic transaction, using savepoints for nested work."""
        self._before_write()
        outermost = self._transaction_depth == 0
        savepoint = ""
        if outermost:
            self.conn.execute("BEGIN IMMEDIATE")
        else:
            self._savepoint_sequence += 1
            savepoint = f"karst_uow_{self._savepoint_sequence}"
            self.conn.execute(f"SAVEPOINT {savepoint}")
        self._transaction_depth += 1
        try:
            yield self
        except BaseException:
            try:
                if outermost:
                    self._rollback_outer_transaction()
                else:
                    self._rollback_savepoint(savepoint)
            finally:
                self._transaction_depth -= 1
            raise
        else:
            try:
                if outermost:
                    self._commit_outer_transaction()
                else:
                    self._release_savepoint(savepoint)
            except BaseException:
                try:
                    if outermost:
                        self._rollback_outer_transaction()
                    else:
                        self._rollback_savepoint(savepoint)
                finally:
                    self._transaction_depth -= 1
                raise
            self._transaction_depth -= 1
