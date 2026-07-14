from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.database import Database

from tests.test_database_migrations import _create_legacy_schema


def test_duplicate_legacy_identities_are_preserved_in_conflict_audit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-conflicts.db"
    _create_legacy_schema(path, wave_one_identity=False)
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO nodes VALUES (2, 1, 1, 'function', 'run', 1, 2)"
    )
    connection.execute("INSERT INTO embeddings VALUES (2, 2, '[9.9, 8.8]')")
    connection.execute("INSERT INTO edges VALUES (1, 1, 1, 2, 'calls')")
    connection.execute(
        "INSERT INTO commits "
        "(id, project_id, commit_hash, message) VALUES (2, 1, 'abc', 'conflict')"
    )
    connection.execute("INSERT INTO commit_files VALUES (2, 2, 'a.py', 'A')")
    connection.commit()
    connection.close()

    with Database(path) as database:
        conflicts = database.conn.execute(
            "SELECT table_name, row_id, reason, payload_json "
            "FROM migration_conflicts ORDER BY table_name, row_id"
        ).fetchall()
        decoded = [
            (row[0], row[1], row[2], json.loads(row[3])) for row in conflicts
        ]

        assert database.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 1
        assert database.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 0
        assert database.conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 1
        assert database.conn.execute("SELECT COUNT(*) FROM commit_files").fetchone()[0] == 1
        assert database.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 1
        assert {item[:3] for item in decoded} >= {
            ("nodes", 1, "duplicate_node_identity"),
            ("nodes", 2, "duplicate_node_identity"),
            ("edges", 1, "collapsed_self_loop"),
            ("commits", 1, "duplicate_commit_identity"),
            ("commits", 2, "duplicate_commit_identity"),
            ("commit_files", 1, "conflicting_commit_file"),
            ("commit_files", 2, "conflicting_commit_file"),
            ("embeddings", 1, "conflicting_node_embedding"),
            ("embeddings", 2, "conflicting_node_embedding"),
        }
        payloads = [payload for *_prefix, payload in decoded]
        assert {payload.get("vector") for payload in payloads} >= {
            "[0.1, 0.2]",
            "[9.9, 8.8]",
        }
        assert {payload.get("message") for payload in payloads} >= {
            "first",
            "conflict",
        }
        assert {payload.get("status") for payload in payloads} >= {"M", "A"}
        audit = database.conn.execute(
            "SELECT conflict_count FROM migration_audit WHERE migration_version = 2"
        ).fetchone()
        assert audit is not None and audit[0] == len(conflicts)
        assert database.integrity_report().ok


def test_duplicate_file_identity_fails_closed_without_version_advance(
    tmp_path: Path,
) -> None:
    path = tmp_path / "duplicate-files.db"
    _create_legacy_schema(path, wave_one_identity=False)
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO files VALUES (2, 1, '/legacy/project/a.py', 'other-hash')"
    )
    connection.commit()
    connection.close()

    try:
        Database(path)
    except RuntimeError as error:
        assert "duplicate file identities" in str(error)
    else:
        raise AssertionError("unsafe duplicate-file migration unexpectedly succeeded")

    verification = sqlite3.connect(path)
    assert verification.execute("PRAGMA user_version").fetchone()[0] == 0
    assert verification.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 2
    assert "files_v2" not in {
        row[0]
        for row in verification.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    verification.close()
