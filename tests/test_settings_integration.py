from __future__ import annotations

from pathlib import Path

import pytest

from src.main import get_db
from src.security import stable_project_id
from src.settings import Settings, SettingsError, TRUSTED_LOCAL_OWNER
from tests.test_security import make_settings


def test_default_database_path_is_independent_of_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    monkeypatch.chdir(first)
    settings_from_first = Settings.from_env({})
    monkeypatch.chdir(second)
    settings_from_second = Settings.from_env({})

    assert settings_from_first.db_path.is_absolute()
    assert settings_from_first.db_path == settings_from_second.db_path


def test_database_connection_uses_configured_path_from_different_working_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    monkeypatch.chdir(first)
    with get_db(settings) as first_db:
        first_db.add_project(
            "same-database",
            str(first),
            TRUSTED_LOCAL_OWNER,
            stable_project_id(first.resolve()),
        )
    monkeypatch.chdir(second)
    with get_db(settings) as second_db:
        row = second_db.conn.execute(
            "SELECT name FROM projects WHERE name = 'same-database'"
        ).fetchone()

    assert row is not None
    assert row[0] == "same-database"


def test_loopback_is_the_default_dashboard_bind() -> None:
    settings = Settings.from_env({})

    assert settings.dashboard_host == "127.0.0.1"
    assert settings.is_dashboard_loopback


def test_remote_dashboard_requires_explicit_opt_in_and_tls() -> None:
    with pytest.raises(SettingsError, match="Remote dashboard binding is disabled"):
        Settings.from_env({"KARST_DASHBOARD_HOST": "0.0.0.0"})

    with pytest.raises(SettingsError, match="requires TLS credentials"):
        Settings.from_env(
            {
                "KARST_DASHBOARD_HOST": "0.0.0.0",
                "KARST_ALLOW_REMOTE_DASHBOARD": "true",
            }
        )
