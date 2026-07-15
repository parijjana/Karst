from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.karst_core.database.database import Database
from src.karst_core.database.db_migrations import MigrationError, migrate
from tests.database_v2_generation_support import create_v2_database


def test_v3_bootstrap_uses_no_filesystem_metadata_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "lexical-only.db"
    create_v2_database(path)
    connection = sqlite3.connect(path)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("filesystem metadata was consulted")

    for method in ("resolve", "absolute", "exists", "is_file", "is_dir", "stat"):
        monkeypatch.setattr(Path, method, forbidden)

    assert migrate(connection) == 3
    connection.close()


def test_v3_migration_preserves_legacy_text_beyond_query_ready_caps(
    tmp_path: Path,
) -> None:
    path = tmp_path / "lossless-legacy.db"
    create_v2_database(path, populated=False)
    absolute_path = "/legacy/project/" + "p" * 5000
    node_type = "legacy type " * 100
    node_name = "名" * 600
    file_hash = "legacy-hash-" + "h" * 5000
    vector = "[" + "0.1," * 2000 + "0.2]"
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO files (id, project_id, path, hash) VALUES (11, 7, ?, ?)",
        (absolute_path, file_hash),
    )
    connection.execute(
        "INSERT INTO nodes "
        "(id, project_id, file_id, type, name, start_line, end_line) "
        "VALUES (21, 7, 11, ?, ?, 1, 2)",
        (node_type, node_name),
    )
    connection.execute(
        "INSERT INTO embeddings "
        "(id, node_id, vector, content_hash, model_revision) "
        "VALUES (41, 21, ?, ?, ?)",
        (vector, "c" * 5000, "model-" + "m" * 5000),
    )
    connection.commit()
    connection.close()

    with Database(path) as database:
        generation = database.conn.execute(
            "SELECT query_ready, manifest_sha256 FROM index_generations "
            "WHERE project_id = 7"
        ).fetchone()
        file_row = database.conn.execute(
            "SELECT path, relative_path, identity_path, hash FROM files WHERE id = 11"
        ).fetchone()
        node_row = database.conn.execute(
            "SELECT type, name, qualified_name FROM nodes WHERE id = 21"
        ).fetchone()
        embedding = database.conn.execute(
            "SELECT vector, content_hash, model_revision FROM embeddings WHERE id = 41"
        ).fetchone()

    assert tuple(generation) == (0, None)
    assert tuple(file_row) == (
        absolute_path,
        "p" * 5000,
        "p" * 5000,
        file_hash,
    )
    assert tuple(node_row) == (node_type, node_name, node_name)
    assert tuple(embedding) == (vector, "c" * 5000, "model-" + "m" * 5000)


@pytest.mark.parametrize(
    ("project_root", "file_path"),
    [
        ("/repo", "/other/a.py"),
        ("/Repo", "/repo/a.py"),
        ("/repo", "/repo"),
        (r"C:\Repo", r"C:\Other\a.py"),
        (r"\\server\share\repo", r"\\server\share\other\a.py"),
    ],
)
def test_lexical_escape_or_root_equal_path_rolls_back_entire_v3_migration(
    tmp_path: Path, project_root: str, file_path: str
) -> None:
    path = tmp_path / "escape.db"
    create_v2_database(path, project_root=project_root, file_path=file_path)
    connection = sqlite3.connect(path)

    with pytest.raises(MigrationError, match="contained|root"):
        migrate(connection)

    assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert tuple(row[1] for row in connection.execute("PRAGMA table_info(files)")) == (
        "id",
        "project_id",
        "path",
        "hash",
    )
    assert connection.execute("SELECT path FROM files").fetchone()[0] == file_path
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'index_generations'"
        ).fetchone()[0]
        == 0
    )
    connection.close()


@pytest.mark.parametrize(
    ("project_root", "file_path", "expected_relative"),
    [
        (r"C:\Repo", r"c:\repo\Src\A.PY", "Src/A.PY"),
        (
            r"\\server\share\Repo",
            r"\\SERVER\SHARE\repo\src\a.py",
            "src/a.py",
        ),
        ("/Repo", "/Repo/src/a.py", "src/a.py"),
    ],
)
def test_windows_and_posix_containment_is_lexical_with_correct_case_rules(
    tmp_path: Path,
    project_root: str,
    file_path: str,
    expected_relative: str,
) -> None:
    path = tmp_path / "contained.db"
    create_v2_database(path, project_root=project_root, file_path=file_path)

    with Database(path) as database:
        assert (
            database.conn.execute("SELECT relative_path FROM files").fetchone()[0]
            == expected_relative
        )


def test_windows_legacy_file_identity_is_case_insensitive(tmp_path: Path) -> None:
    stable_ids: list[str] = []
    for name, root, file_path in (
        ("upper.db", r"C:\Repo", r"C:\Repo\Src\A.PY"),
        ("lower.db", r"c:\repo", r"c:\repo\src\a.py"),
    ):
        path = tmp_path / name
        create_v2_database(path, project_root=root, file_path=file_path)
        with Database(path) as database:
            stable_ids.append(
                str(database.conn.execute("SELECT stable_id FROM files").fetchone()[0])
            )

    assert stable_ids[0] == stable_ids[1]


def test_tampered_v2_is_validated_before_v3_mutation(tmp_path: Path) -> None:
    path = tmp_path / "tampered-v2.db"
    create_v2_database(path)
    connection = sqlite3.connect(path)
    connection.execute("DROP INDEX ix_edges_source")
    connection.commit()

    with pytest.raises(MigrationError, match="missing index"):
        migrate(connection)

    assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'index_generations'"
        ).fetchone()[0]
        == 0
    )
    assert connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 1
    connection.close()
