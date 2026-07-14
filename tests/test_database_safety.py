from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from src.database import Database
from src.db_transaction import DatabaseConcurrencyError, DatabaseTransactionError
from src.settings import TRUSTED_LOCAL_OWNER


def _project(database: Database, name: str) -> int:
    return database.add_project(
        name,
        f"/projects/{name}",
        TRUSTED_LOCAL_OWNER,
        f"stable:{name}",
    )


def test_repository_is_thread_affine_and_owner_remains_usable(tmp_path: Path) -> None:
    database = Database(tmp_path / "thread-affine.db")
    failures: list[BaseException] = []

    def use_from_non_owner() -> None:
        try:
            _project(database, "wrong-thread")
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=use_from_non_owner)
    worker.start()
    worker.join()

    assert len(failures) == 1
    assert isinstance(failures[0], DatabaseConcurrencyError)
    assert _project(database, "owner-thread") > 0
    database.close()


def test_concurrent_initializers_serialize_and_validate_one_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "concurrent-init.db"
    barrier = threading.Barrier(2)

    def initialize() -> tuple[int, int]:
        barrier.wait()
        with Database(path) as database:
            return database.schema_version, database.conn.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _item: initialize(), range(2)))

    assert results == ((3, 3), (3, 3))


def test_outer_commit_failure_rolls_back_and_resets_unit_of_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Database(tmp_path / "commit-failure.db") as database:
        real_commit = database._commit_outer_transaction

        def fail_commit() -> None:
            raise sqlite3.OperationalError("injected commit failure")

        monkeypatch.setattr(database, "_commit_outer_transaction", fail_commit)
        with pytest.raises(sqlite3.OperationalError, match="injected commit"):
            with database.transaction():
                _project(database, "rolled-back")

        assert database.transaction_depth == 0
        assert not database.conn.in_transaction
        assert database.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0

        monkeypatch.setattr(database, "_commit_outer_transaction", real_commit)
        with database.transaction():
            _project(database, "recovered")
        assert database.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1


def test_savepoint_release_failure_rolls_back_and_resets_nested_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Database(tmp_path / "release-failure.db") as database:
        real_release = database._release_savepoint
        release_calls = 0

        def fail_first_release(name: str) -> None:
            nonlocal release_calls
            release_calls += 1
            if release_calls == 1:
                raise sqlite3.OperationalError("injected release failure")
            real_release(name)

        monkeypatch.setattr(database, "_release_savepoint", fail_first_release)
        with pytest.raises(sqlite3.OperationalError, match="injected release"):
            with database.transaction():
                project_id = _project(database, "rolled-back")
                with database.transaction():
                    database.add_file(project_id, "/projects/rolled-back/a.py", "hash")

        assert database.transaction_depth == 0
        assert not database.conn.in_transaction
        assert database.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


def test_external_transaction_is_never_committed_by_repository_wrapper(
    tmp_path: Path,
) -> None:
    path = tmp_path / "external-transaction.db"
    with Database(path) as database:
        database.conn.execute("BEGIN")
        database.conn.execute(
            "INSERT INTO projects (name, path, owner, stable_id) VALUES (?, ?, ?, ?)",
            ("raw", "/raw", TRUSTED_LOCAL_OWNER, "stable:raw"),
        )

        with pytest.raises(DatabaseTransactionError, match="external transaction"):
            database.add_file(1, "/raw/a.py", "hash")

        observer = sqlite3.connect(path)
        assert observer.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
        observer.close()
        database.conn.rollback()
        assert database.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


def test_repository_and_schema_reject_cross_project_node_and_edges(
    tmp_path: Path,
) -> None:
    with Database(tmp_path / "project-consistency.db") as database:
        first = _project(database, "first")
        second = _project(database, "second")
        first_file = database.add_file(first, "/projects/first/a.py", "hash")
        second_file = database.add_file(second, "/projects/second/b.py", "hash")
        first_node = database.add_node(first, first_file, "function", "first", 1, 1)
        second_node = database.add_node(second, second_file, "function", "second", 1, 1)

        with pytest.raises(ValueError, match="does not belong to project"):
            database.add_node(first, second_file, "function", "bad", 2, 2)
        with pytest.raises(ValueError, match="do not belong to project"):
            database.add_edge(first, first_node, second_node, "calls")
        with pytest.raises(sqlite3.IntegrityError):
            database.conn.execute(
                "INSERT INTO nodes "
                "(project_id, file_id, type, name, start_line, end_line) "
                "VALUES (?, ?, 'function', 'raw-bad', 3, 3)",
                (first, second_file),
            )


def test_integrity_report_detects_cross_project_corruption(tmp_path: Path) -> None:
    with Database(tmp_path / "integrity-consistency.db") as database:
        first = _project(database, "first")
        second = _project(database, "second")
        second_file = database.add_file(second, "/projects/second/b.py", "hash")
        first_generation = database.conn.execute(
            "SELECT id FROM index_generations "
            "WHERE project_id = ? AND status = 'active'",
            (first,),
        ).fetchone()[0]
        database.conn.execute("PRAGMA foreign_keys = OFF")
        database.conn.execute(
            "INSERT INTO nodes "
            "(project_id, generation_id, file_id, stable_id, language, type, name, "
            "qualified_name, start_line, end_line) VALUES (?, ?, ?, ?, 'python', "
            "'function', 'corrupt', 'corrupt', 1, 1)",
            (
                first,
                first_generation,
                second_file,
                str(uuid5(NAMESPACE_URL, "corrupt-node")),
            ),
        )
        database.conn.execute("PRAGMA foreign_keys = ON")

        report = database.integrity_report()
        assert not report.ok
        assert report.consistency_violations
