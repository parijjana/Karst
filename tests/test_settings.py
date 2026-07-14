from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.settings import Settings, SettingsError


def settings_values(tmp_path: Path, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "data_dir": tmp_path / "data",
        "db_path": tmp_path / "data" / "karst.db",
        "allowed_roots": (tmp_path,),
        "dashboard_host": "127.0.0.1",
        "dashboard_port": 8085,
        "allowed_hosts": ("127.0.0.1",),
        "allowed_origins": ("http://127.0.0.1:8085",),
    }
    values.update(overrides)
    return values


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(**settings_values(tmp_path, **overrides))  # type: ignore[arg-type]


def test_from_env_normalizes_custom_paths_lists_and_limits(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    configured = Settings.from_env(
        {
            "KARST_DATA_DIR": str(tmp_path / "state"),
            "KARST_DB_PATH": str(tmp_path / "state" / "graph.db"),
            "KARST_ALLOWED_ROOTS": os.pathsep.join((str(first), str(second))),
            "KARST_DASHBOARD_HOST": "localhost",
            "KARST_DASHBOARD_PORT": "9090",
            "KARST_ALLOWED_HOSTS": "localhost, 127.0.0.1",
            "KARST_ALLOWED_ORIGINS": "http://localhost:9090",
            "KARST_ADMIN_REQUESTS_PER_MINUTE": "12",
            "KARST_ADMIN_SESSION_TTL_SECONDS": "600",
            "KARST_DASHBOARD_DEFAULT_PAGE_SIZE": "25",
            "KARST_DASHBOARD_MAX_PAGE_SIZE": "50",
            "KARST_ALLOW_REMOTE_DASHBOARD": "off",
        }
    )

    assert configured.data_dir == (tmp_path / "state").resolve()
    assert configured.db_path == (tmp_path / "state" / "graph.db").resolve()
    assert configured.allowed_roots == (first.resolve(), second.resolve())
    assert configured.allowed_hosts == ("localhost", "127.0.0.1")
    assert configured.allowed_origins == ("http://localhost:9090",)
    assert configured.dashboard_port == 9090
    assert configured.admin_requests_per_minute == 12
    assert configured.admin_session_ttl_seconds == 600
    assert configured.dashboard_default_page_size == 25
    assert configured.dashboard_max_page_size == 50
    assert configured.is_dashboard_loopback


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"allowed_roots": ()}, "At least one allowed root"),
        ({"allowed_roots": (Path("missing-root"),)}, "allowed root is unavailable"),
        ({"dashboard_port": 0}, "Dashboard port is invalid"),
        ({"allowed_hosts": ()}, "Trusted hosts are invalid"),
        ({"allowed_hosts": ("x" * 254,)}, "Trusted hosts are invalid"),
        ({"allowed_origins": ("ftp://example.test",)}, "allowed origin is invalid"),
        ({"allowed_origins": ("http://localhost/path",)}, "allowed origin is invalid"),
        ({"admin_token": "a" * 32}, "configured together"),
        (
            {"admin_token": "short", "csrf_token": "different-but-short"},
            "too short",
        ),
        ({"admin_token": "a" * 32, "csrf_token": "a" * 32}, "distinct"),
        ({"tls_certfile": Path("certificate.pem")}, "both required"),
        ({"admin_requests_per_minute": 0}, "rate limit is invalid"),
        ({"admin_session_ttl_seconds": 59}, "session lifetime is invalid"),
        (
            {"dashboard_default_page_size": 11, "dashboard_max_page_size": 10},
            "default page size is invalid",
        ),
        ({"dashboard_max_page_size": 501}, "maximum page size is invalid"),
    ],
)
def test_settings_reject_unsafe_values(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(SettingsError, match=message):
        make_settings(tmp_path, **overrides)


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"KARST_OWNER_ID": "client"}, "one trusted local stdio domain"),
        ({"KARST_ALLOW_REMOTE_DASHBOARD": "maybe"}, "Invalid boolean"),
        ({"KARST_DASHBOARD_PORT": "many"}, "Dashboard port is invalid"),
        (
            {"KARST_ADMIN_REQUESTS_PER_MINUTE": "many"},
            "Admin rate limit is invalid",
        ),
    ],
)
def test_from_env_rejects_invalid_text_values(
    env: dict[str, str], message: str
) -> None:
    with pytest.raises(SettingsError, match=message):
        Settings.from_env(env)


def test_remote_settings_require_pinned_tls_and_https_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    certificate = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    certificate.write_text("certificate", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    monkeypatch.setattr("src.settings._validate_tls_pair", lambda *_: None)
    remote = {
        "dashboard_host": "0.0.0.0",
        "allow_remote_dashboard": True,
        "tls_certfile": certificate,
        "tls_keyfile": key,
        "admin_token": "a" * 32,
        "csrf_token": "c" * 32,
        "allowed_hosts": ("graph.example",),
        "allowed_origins": ("https://graph.example",),
    }

    configured = make_settings(tmp_path, **remote)
    assert not configured.is_dashboard_loopback
    assert configured.tls_certfile == certificate.resolve()

    with pytest.raises(SettingsError, match="Wildcard trusted hosts"):
        make_settings(tmp_path, **{**remote, "allowed_hosts": ("*",)})
    with pytest.raises(SettingsError, match="origins must use HTTPS"):
        make_settings(
            tmp_path,
            **{**remote, "allowed_origins": ("http://graph.example",)},
        )


def test_tls_credentials_must_exist_and_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SettingsError, match="credentials are unavailable"):
        make_settings(
            tmp_path,
            tls_certfile=tmp_path / "missing.crt",
            tls_keyfile=tmp_path / "missing.key",
        )

    certificate = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    certificate.write_text("certificate", encoding="utf-8")
    key.write_text("key", encoding="utf-8")

    def reject_pair(*_args: object) -> None:
        raise SettingsError("Dashboard TLS credentials are invalid.")

    monkeypatch.setattr("src.settings._validate_tls_pair", reject_pair)
    with pytest.raises(SettingsError, match="credentials are invalid"):
        make_settings(tmp_path, tls_certfile=certificate, tls_keyfile=key)
