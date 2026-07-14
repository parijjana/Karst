from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from src.security import (
    PathSecurityPolicy,
    SecurityViolation,
    stable_project_id,
)
from src.settings import Settings, TRUSTED_LOCAL_OWNER


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "data_dir": tmp_path / "data",
        "db_path": tmp_path / "data" / "karst.db",
        "allowed_roots": (tmp_path,),
        "dashboard_host": "127.0.0.1",
        "dashboard_port": 8085,
        "allowed_hosts": ("127.0.0.1",),
        "allowed_origins": ("http://127.0.0.1:8085",),
        "admin_token": None,
        "csrf_token": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_policy_rejects_outside_root_and_traversal_without_leaking_path(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "private" / "secret.py"
    allowed.mkdir()
    outside.parent.mkdir()
    outside.write_text("SECRET = True\n", encoding="utf-8")
    policy = PathSecurityPolicy((allowed,))

    with pytest.raises(SecurityViolation) as outside_error:
        policy.validate_project_file(outside, allowed)
    with pytest.raises(SecurityViolation) as traversal_error:
        policy.validate_project_file(Path("..") / "private" / "secret.py", allowed)

    assert str(outside) not in str(outside_error.value)
    assert "private" not in str(traversal_error.value)
    assert traversal_error.value.code == "path_traversal_not_allowed"


def test_policy_rejects_link_escape(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (outside / "secret.py").write_text("SECRET = True\n", encoding="utf-8")
    link = allowed / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"filesystem cannot create a test link: {error}")

    policy = PathSecurityPolicy((allowed,))
    with pytest.raises(SecurityViolation, match="link_not_allowed"):
        policy.validate_project_file(link / "secret.py", allowed)


def test_rejected_cross_project_update_does_not_mutate_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src import main

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_file = first / "first.py"
    second_file = second / "second.py"
    first_file.write_text("def first():\n    pass\n", encoding="utf-8")
    second_file.write_text("def second():\n    pass\n", encoding="utf-8")
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main, "settings", settings)

    assert main.index_project("first", str(first)).startswith("Indexed")
    assert main.index_project("second", str(second)).startswith("Indexed")

    with closing(sqlite3.connect(settings.db_path)) as conn:
        before = conn.execute(
            "SELECT path, hash FROM files ORDER BY project_id, path"
        ).fetchall()

    result = main.update_graph("first", [str(second_file)])

    with closing(sqlite3.connect(settings.db_path)) as conn:
        after = conn.execute(
            "SELECT path, hash FROM files ORDER BY project_id, path"
        ).fetchall()
    assert result == "Security error [project_boundary_violation]."
    assert after == before

    traversal_result = main.update_graph(
        "first", [str(Path("..") / "second" / "second.py")]
    )
    with closing(sqlite3.connect(settings.db_path)) as conn:
        after_traversal = conn.execute(
            "SELECT path, hash FROM files ORDER BY project_id, path"
        ).fetchall()
    assert traversal_result == "Security error [path_traversal_not_allowed]."
    assert after_traversal == before


@pytest.mark.parametrize(
    "legacy_kind", ["relative_path", "missing_identity", "missing_path"]
)
def test_legacy_project_is_quarantined_before_reindex_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    legacy_kind: str,
) -> None:
    from src import main

    project = tmp_path / "project"
    project.mkdir()
    source = project / "source.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main, "settings", settings)
    db = main.get_db(settings)
    if legacy_kind == "relative_path":
        stored_path = "."
        stored_identity = stable_project_id(project)
    elif legacy_kind == "missing_identity":
        stored_path = str(project)
        stored_identity = None
    else:
        missing_path = tmp_path / "missing-project"
        stored_path = str(missing_path)
        stored_identity = stable_project_id(missing_path)
    cursor = db.conn.cursor()
    cursor.execute(
        "INSERT INTO projects (name, path, owner, stable_id) VALUES (?, ?, ?, ?)",
        ("legacy", stored_path, TRUSTED_LOCAL_OWNER, stored_identity),
    )
    project_id = cursor.lastrowid
    # The v3 compatibility tables require every file to belong to a
    # generation.  A staging generation preserves the legacy fixture's
    # quarantine semantics without making it queryable.
    cursor.execute(
        "INSERT INTO index_generations "
        "(project_id, ordinal, status) VALUES (?, ?, ?)",
        (project_id, 1, "staging"),
    )
    generation_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO files "
        "(project_id, generation_id, stable_id, path, relative_path, "
        "identity_path, hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            project_id,
            generation_id,
            stable_project_id(source),
            str(source),
            source.name,
            source.name,
            "preserved-hash",
        ),
    )
    db.conn.commit()
    db.close()

    result = main.index_project("legacy", str(project))

    with closing(sqlite3.connect(settings.db_path)) as conn:
        remaining = conn.execute("SELECT path, hash FROM files").fetchall()
    assert result == "Security error [project_identity_invalid]."
    assert remaining == [(str(source), "preserved-hash")]


def test_outside_root_and_link_reindex_fail_before_existing_data_is_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src import main

    allowed = tmp_path / "allowed"
    project = allowed / "project"
    outside = tmp_path / "outside"
    project.mkdir(parents=True)
    outside.mkdir()
    source = project / "source.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    outside_source = outside / "outside.py"
    outside_source.write_text("SECRET = True\n", encoding="utf-8")
    settings = make_settings(tmp_path, allowed_roots=(allowed,))
    monkeypatch.setattr(main, "settings", settings)
    assert main.index_project("stable", str(project)).startswith("Indexed")

    with closing(sqlite3.connect(settings.db_path)) as conn:
        before = conn.execute("SELECT path, hash FROM files").fetchall()

    outside_result = main.index_project("stable", str(outside))
    with closing(sqlite3.connect(settings.db_path)) as conn:
        after_outside = conn.execute("SELECT path, hash FROM files").fetchall()
    assert outside_result == "Security error [path_not_allowed]."
    assert after_outside == before

    link = project / "linked.py"
    try:
        link.symlink_to(outside_source)
    except OSError as error:
        pytest.skip(f"filesystem cannot create a test link: {error}")
    link_result = main.index_project("stable", str(project))
    with closing(sqlite3.connect(settings.db_path)) as conn:
        after_link = conn.execute("SELECT path, hash FROM files").fetchall()
    assert link_result == "Security error [link_not_allowed]."
    assert after_link == before


def test_index_result_reports_typed_parser_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src import main

    project = tmp_path / "broken"
    project.mkdir()
    (project / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main, "settings", settings)

    result = main.index_project("broken", str(project))

    assert result == "Indexed 0 files for project 'broken'; skipped 0; failed 1."


@pytest.mark.parametrize(
    "invalid_identity",
    ["relative_path", "missing_stable_id", "mismatched_stable_id", "wrong_owner"],
)
def test_git_backfill_rejects_untrusted_registered_project_before_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_identity: str,
) -> None:
    from src import main

    project = tmp_path / "project"
    project.mkdir()
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main, "settings", settings)
    stored_path = str(project)
    stored_owner = TRUSTED_LOCAL_OWNER
    stored_identity: str | None = stable_project_id(project)
    if invalid_identity == "relative_path":
        stored_path = "."
    elif invalid_identity == "missing_stable_id":
        stored_identity = None
    elif invalid_identity == "mismatched_stable_id":
        stored_identity = "not-the-project-identity"
    else:
        stored_owner = "pretend-client"

    db = main.get_db(settings)
    db.conn.execute(
        "INSERT INTO projects (name, path, owner, stable_id) VALUES (?, ?, ?, ?)",
        ("unsafe-git", stored_path, stored_owner, stored_identity),
    )
    db.conn.commit()
    db.close()
    subprocess_called = False

    def fake_backfill(*_args: object) -> str:
        nonlocal subprocess_called
        subprocess_called = True
        return "unsafe"

    monkeypatch.setattr(main, "do_backfill_git_history", fake_backfill)

    result = main.backfill_git_history("unsafe-git")

    assert result == "Security error [project_identity_invalid]."
    assert not subprocess_called
