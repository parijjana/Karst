from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterator

import pytest
import uvicorn
from fastapi import Request

from src.karst_core.database.database import Database
from src.security import stable_project_id
from src.settings import Settings, TRUSTED_LOCAL_OWNER
from src.web import create_app


ADMIN_TOKEN = "a" * 32
API_CSRF_TOKEN = "c" * 32


class TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def find_browser() -> str | None:
    candidates = (
        shutil.which("chrome"),
        shutil.which("msedge"),
        shutil.which("chromium"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    )
    return next(
        (
            str(candidate)
            for candidate in candidates
            if candidate and Path(candidate).is_file()
        ),
        None,
    )


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextmanager
def run_server(app: Any, port: int) -> Iterator[None]:
    configuration = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
    )
    server = uvicorn.Server(configuration)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        raise RuntimeError("Browser test server did not start")
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_hostile_repository_values_render_as_text_without_admin_request(
    tmp_path: Path,
) -> None:
    browser = find_browser()
    if browser is None:
        pytest.skip("Chrome/Edge is unavailable for hostile-value browser proof")

    port = reserve_port()
    origin = f"http://127.0.0.1:{port}"
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "karst.db",
        allowed_roots=(tmp_path,),
        dashboard_host="127.0.0.1",
        dashboard_port=port,
        allowed_hosts=("127.0.0.1",),
        allowed_origins=(origin,),
        admin_token=ADMIN_TOKEN,
        csrf_token=API_CSRF_TOKEN,
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    xss_payload = "<img src=x onerror=document.body.dataset.karstXss=1>"
    admin_payload = (
        "<img src=x onerror=fetch('/api/admin/scripts/watchdog/start',"
        "{method:'POST'})>"
    )
    database = Database(str(settings.db_path))
    project_id = database.add_project(
        xss_payload,
        str(tmp_path),
        TRUSTED_LOCAL_OWNER,
        stable_project_id(tmp_path.resolve()),
    )
    file_id = database.add_file(project_id, str(tmp_path / "hostile.py"), "hash")
    database.add_node(project_id, file_id, "class", xss_payload, 1, 1)
    database.log_telemetry(
        project_id,
        "service:hostile",
        1.0,
        1,
        admin_payload,
    )
    database.close()

    app = create_app(settings)
    admin_attempts: list[str] = []

    @app.middleware("http")
    async def observe_admin_requests(request: Request, call_next):
        if (
            request.method == "POST"
            and request.url.path == "/api/admin/scripts/watchdog/start"
        ):
            admin_attempts.append(request.url.path)
        return await call_next(request)

    profile = tmp_path / "chrome-profile"
    profile.mkdir()
    command = [
        browser,
        "--headless=new",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-sync",
        "--no-first-run",
        f"--user-data-dir={profile}",
        "--virtual-time-budget=5000",
        "--dump-dom",
        f"{origin}/",
    ]

    with run_server(app, port):
        result = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=30,
        )

    assert result.returncode == 0, result.stderr
    assert 'data-karst-dashboard-ready="complete"' in result.stdout.lower()
    collector = TextCollector()
    collector.feed(result.stdout)
    rendered_text = "".join(collector.parts)
    assert xss_payload in rendered_text
    assert admin_payload in rendered_text
    assert 'data-karst-xss="1"' not in result.stdout.lower()
    assert admin_attempts == []
