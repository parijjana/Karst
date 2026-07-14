from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from src.settings import Settings
from src.web import create_app, run_dashboard


ADMIN_TOKEN = "a" * 32
CSRF_TOKEN = "c" * 32


def make_app(tmp_path: Path, **overrides: Any):
    values: dict[str, Any] = dict(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "karst.db",
        allowed_roots=(tmp_path,),
        dashboard_host="127.0.0.1",
        dashboard_port=8085,
        allowed_hosts=("127.0.0.1",),
        allowed_origins=("http://127.0.0.1:8085",),
        admin_token=ADMIN_TOKEN,
        csrf_token=CSRF_TOKEN,
    )
    values.update(overrides)
    settings = Settings(**values)
    return create_app(settings)


def test_unauthorized_admin_mutation_is_rejected(tmp_path: Path) -> None:
    client = TestClient(make_app(tmp_path), base_url="http://127.0.0.1")

    response = client.post("/api/admin/scripts/watchdog/start")

    assert response.status_code == 401
    assert response.json() == {"detail": "Admin authorization required."}


def test_cross_origin_admin_mutation_is_rejected(tmp_path: Path) -> None:
    client = TestClient(make_app(tmp_path), base_url="http://127.0.0.1")

    response = client.post(
        "/api/admin/scripts/watchdog/start",
        headers={
            "Authorization": f"Bearer {ADMIN_TOKEN}",
            "X-CSRF-Token": CSRF_TOKEN,
            "Origin": "https://attacker.invalid",
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Cross-origin admin request denied."}


def test_missing_csrf_capability_is_rejected(tmp_path: Path) -> None:
    client = TestClient(make_app(tmp_path), base_url="http://127.0.0.1")

    response = client.post(
        "/api/admin/scripts/watchdog/start",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed."}


def test_untrusted_host_is_rejected(tmp_path: Path) -> None:
    client = TestClient(make_app(tmp_path), base_url="http://attacker.invalid")

    response = client.get("/api/stats")

    assert response.status_code == 400


def test_loopback_read_api_remains_available_over_http(tmp_path: Path) -> None:
    client = TestClient(make_app(tmp_path), base_url="http://127.0.0.1")

    response = client.get("/api/stats")

    assert response.status_code == 200


def test_repeated_admin_mutations_are_throttled(
    tmp_path: Path, monkeypatch
) -> None:
    async def fake_start_script(script_name: str) -> dict[str, str]:
        return {"status": "started", "script": script_name}

    monkeypatch.setattr("src.web.start_script", fake_start_script)
    client = TestClient(
        make_app(tmp_path, admin_requests_per_minute=1),
        base_url="http://127.0.0.1",
    )
    headers = {
        "Authorization": f"Bearer {ADMIN_TOKEN}",
        "X-CSRF-Token": CSRF_TOKEN,
    }

    first = client.post("/api/admin/scripts/watchdog/start", headers=headers)
    second = client.post("/api/admin/scripts/watchdog/start", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {"detail": "Admin request rate exceeded."}


def test_remote_dashboard_requires_tls_and_protects_reads(
    tmp_path: Path, monkeypatch
) -> None:
    certificate = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    certificate.write_text("test certificate", encoding="utf-8")
    key.write_text("test key", encoding="utf-8")
    monkeypatch.setattr("src.settings._validate_tls_pair", lambda *_: None)
    remote_app = make_app(
        tmp_path,
        dashboard_host="0.0.0.0",
        allowed_hosts=("graph.example",),
        allowed_origins=("https://graph.example",),
        allow_remote_dashboard=True,
        tls_certfile=certificate,
        tls_keyfile=key,
    )
    client = TestClient(remote_app, base_url="https://graph.example")

    unauthorized = client.get("/api/stats")
    missing_origin = client.get(
        "/api/stats", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
    )
    authorized = client.get(
        "/api/stats",
        headers={
            "Authorization": f"Bearer {ADMIN_TOKEN}",
            "Origin": "https://graph.example",
        },
    )

    assert unauthorized.status_code == 401
    assert missing_origin.status_code == 403
    assert authorized.status_code == 200


def test_remote_dashboard_passes_tls_files_to_uvicorn(
    tmp_path: Path, monkeypatch
) -> None:
    certificate = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    certificate.write_text("test certificate", encoding="utf-8")
    key.write_text("test key", encoding="utf-8")
    monkeypatch.setattr("src.settings._validate_tls_pair", lambda *_: None)
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "karst.db",
        allowed_roots=(tmp_path,),
        dashboard_host="0.0.0.0",
        dashboard_port=8443,
        allowed_hosts=("graph.example",),
        allowed_origins=("https://graph.example",),
        admin_token=ADMIN_TOKEN,
        csrf_token=CSRF_TOKEN,
        allow_remote_dashboard=True,
        tls_certfile=certificate,
        tls_keyfile=key,
    )
    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("src.web.uvicorn.run", fake_run)
    run_dashboard(settings)

    assert captured["ssl_certfile"] == str(certificate.resolve())
    assert captured["ssl_keyfile"] == str(key.resolve())
