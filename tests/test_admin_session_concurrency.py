from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web import create_app
from tests.test_admin_session_security import ORIGIN, login, make_settings


def test_concurrent_mutations_consume_session_csrf_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_entered = threading.Event()
    second_entered = threading.Event()
    release = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    async def blocked_start(script_name: str) -> dict[str, str]:
        nonlocal call_count
        assert script_name == "watchdog"
        with call_lock:
            call_count += 1
            first_entered.set()
            if call_count == 2:
                second_entered.set()
        await asyncio.get_running_loop().run_in_executor(None, release.wait)
        return {"status": "started", "script": script_name}

    monkeypatch.setattr("src.web.start_script", blocked_start)
    with TestClient(
        create_app(make_settings(tmp_path)), base_url=ORIGIN
    ) as client:
        csrf = login(client).json()["csrf_token"]
        headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                client.post, "/api/admin/scripts/watchdog/start", headers=headers
            )
            assert first_entered.wait(timeout=2)
            second = executor.submit(
                client.post, "/api/admin/scripts/watchdog/start", headers=headers
            )
            second_entered.wait(timeout=0.25)
            release.set()
            responses = (first.result(timeout=2), second.result(timeout=2))

        assert sorted(response.status_code for response in responses) == [200, 403]
        successful = next(response for response in responses if response.status_code == 200)
        rejected = next(response for response in responses if response.status_code == 403)
        replacement = successful.headers["X-CSRF-Token"]
        assert replacement != csrf
        assert "X-CSRF-Token" not in rejected.headers
        assert call_count == 1

        follow_up = client.post(
            "/api/admin/scripts/watchdog/start",
            headers={"Origin": ORIGIN, "X-CSRF-Token": replacement},
        )
        assert follow_up.status_code == 200
