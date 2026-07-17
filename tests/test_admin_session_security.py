from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.settings import Settings
from src.web import DASHBOARD_CSP, _constant_time_secret_match, create_app


ADMIN_TOKEN = "a" * 32
API_CSRF_TOKEN = "c" * 32
ORIGIN = "http://127.0.0.1:8085"


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "db_path": tmp_path / "data" / "karst.db",
        "allowed_roots": (tmp_path,),
        "dashboard_host": "127.0.0.1",
        "dashboard_port": 8085,
        "allowed_hosts": ("127.0.0.1",),
        "allowed_origins": (ORIGIN,),
        "admin_token": ADMIN_TOKEN,
        "csrf_token": API_CSRF_TOKEN,
        "admin_session_ttl_seconds": 300,
    }
    values.update(overrides)
    return Settings(**values)


def login(client: TestClient, capability: str = ADMIN_TOKEN):
    return client.post(
        "/api/admin/session/login",
        json={"capability": capability},
        headers={"Origin": ORIGIN},
    )


def test_login_uses_constant_length_digest_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[bytes, bytes]] = []

    def fake_compare(left: bytes, right: bytes) -> bool:
        observed.append((left, right))
        return False

    monkeypatch.setattr("src.web.secrets.compare_digest", fake_compare)

    assert not _constant_time_secret_match("short", ADMIN_TOKEN)
    assert len(observed) == 1
    assert len(observed[0][0]) == len(observed[0][1]) == 32


def test_invalid_login_is_generic_and_sets_no_cookie(tmp_path: Path) -> None:
    client = TestClient(create_app(make_settings(tmp_path)), base_url=ORIGIN)

    response = login(client, "wrong capability")

    assert response.status_code == 401
    assert response.json() == {"detail": "Admin authorization required."}
    assert "set-cookie" not in response.headers


def test_login_sets_only_opaque_httponly_strict_cookie_and_returns_csrf(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(make_settings(tmp_path)), base_url=ORIGIN)

    response = login(client)

    assert response.status_code == 200
    csrf_token = response.json()["csrf_token"]
    assert isinstance(csrf_token, str)
    assert len(csrf_token) >= 32
    cookie = response.headers["set-cookie"]
    assert "karst_admin_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie
    assert ADMIN_TOKEN not in cookie
    assert API_CSRF_TOKEN not in cookie


def test_cross_origin_login_is_rejected(tmp_path: Path) -> None:
    client = TestClient(create_app(make_settings(tmp_path)), base_url=ORIGIN)

    response = client.post(
        "/api/admin/session/login",
        json={"capability": ADMIN_TOKEN},
        headers={"Origin": "https://attacker.invalid"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Cross-origin admin request denied."}


def test_session_mutation_requires_csrf_and_rotates_it_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def fake_start(script_name: str) -> dict[str, str]:
        calls.append(script_name)
        return {"status": "started", "script": script_name}

    monkeypatch.setattr("src.web.start_script", fake_start)
    client = TestClient(create_app(make_settings(tmp_path)), base_url=ORIGIN)
    first_csrf = login(client).json()["csrf_token"]

    missing_csrf = client.post(
        "/api/admin/scripts/watchdog/start", headers={"Origin": ORIGIN}
    )
    successful = client.post(
        "/api/admin/scripts/watchdog/start",
        headers={"Origin": ORIGIN, "X-CSRF-Token": first_csrf},
    )
    rotated_csrf = successful.headers["X-CSRF-Token"]
    replay = client.post(
        "/api/admin/scripts/watchdog/start",
        headers={"Origin": ORIGIN, "X-CSRF-Token": first_csrf},
    )
    second_success = client.post(
        "/api/admin/scripts/watchdog/start",
        headers={"Origin": ORIGIN, "X-CSRF-Token": rotated_csrf},
    )

    assert missing_csrf.status_code == 403
    assert missing_csrf.json() == {"detail": "CSRF validation failed."}
    assert successful.status_code == 200
    assert rotated_csrf != first_csrf
    assert replay.status_code == 403
    assert second_success.status_code == 200
    assert calls == ["watchdog", "watchdog"]


def test_same_origin_session_bootstrap_rotates_csrf_without_browser_storage(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(make_settings(tmp_path)), base_url=ORIGIN)
    first_csrf = login(client).json()["csrf_token"]

    refreshed = client.post(
        "/api/admin/session/csrf", headers={"Origin": ORIGIN}
    )

    assert refreshed.status_code == 200
    assert refreshed.json()["csrf_token"] != first_csrf


def test_logout_invalidates_session_and_clears_cookie(tmp_path: Path) -> None:
    client = TestClient(create_app(make_settings(tmp_path)), base_url=ORIGIN)
    csrf_token = login(client).json()["csrf_token"]

    response = client.delete(
        "/api/admin/session",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_token},
    )
    after_logout = client.post(
        "/api/admin/scripts/watchdog/start",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 204
    assert "karst_admin_session=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert after_logout.status_code == 401


def test_logout_remains_available_after_mutation_rate_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_start(script_name: str) -> dict[str, str]:
        return {"status": "started", "script": script_name}

    monkeypatch.setattr("src.web.start_script", fake_start)
    settings = make_settings(tmp_path, admin_requests_per_minute=1)
    client = TestClient(create_app(settings), base_url=ORIGIN)
    csrf_token = login(client).json()["csrf_token"]
    mutation = client.post(
        "/api/admin/scripts/watchdog/start",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_token},
    )
    rotated_csrf = mutation.headers["X-CSRF-Token"]

    logout = client.delete(
        "/api/admin/session",
        headers={"Origin": ORIGIN, "X-CSRF-Token": rotated_csrf},
    )

    assert mutation.status_code == 200
    assert logout.status_code == 204


def test_remote_login_cookie_is_secure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    certificate = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    certificate.write_text("test certificate", encoding="utf-8")
    key.write_text("test key", encoding="utf-8")
    monkeypatch.setattr("src.settings._validate_tls_pair", lambda *_: None)
    origin = "https://graph.example"
    settings = make_settings(
        tmp_path,
        dashboard_host="0.0.0.0",
        dashboard_port=443,
        allowed_hosts=("graph.example",),
        allowed_origins=(origin,),
        allow_remote_dashboard=True,
        tls_certfile=certificate,
        tls_keyfile=key,
    )
    client = TestClient(create_app(settings), base_url=origin)

    response = client.post(
        "/api/admin/session/login",
        json={"capability": ADMIN_TOKEN},
        headers={"Origin": origin},
    )
    session_read = client.get("/api/stats")

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]
    assert session_read.status_code == 200


def test_security_headers_are_exact_on_html_and_error_responses(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(make_settings(tmp_path)), base_url=ORIGIN)
    expected_csp = (
        "default-src 'none'; "
        "script-src 'sha256-ATPUJAUfzc1fzQSj49Sy4FuMUdG0pX6wTovPRVFYi5A='; "
        "style-src 'unsafe-inline'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'none'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'; "
        "manifest-src 'none'; "
        "worker-src 'none'; "
        "media-src 'none'"
    )

    dashboard = client.get("/")
    error = client.get("/missing")

    for response in (dashboard, error):
        assert response.headers["Content-Security-Policy"] == expected_csp
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
    assert DASHBOARD_CSP == expected_csp
