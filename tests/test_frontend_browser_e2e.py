from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import Request

from src.karst_core.database.database import Database
from src.security import stable_project_id
from src.settings import Settings, TRUSTED_LOCAL_OWNER
from src.web import create_app
from tests.frontend_browser_support import (
    DASHBOARD_BEHAVIOR_PRELUDE,
    TextCollector,
    dump_instrumented_dashboard,
    find_browser,
    instrumented_dashboard,
    reserve_port,
    run_browser_command,
    run_server,
)


ADMIN_TOKEN = "a" * 32
API_CSRF_TOKEN = "c" * 32


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
        "--disable-breakpad",
        "--disable-component-update",
        "--disable-crash-reporter",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-gpu",
        "--disable-sync",
        "--no-sandbox",
        "--no-first-run",
        f"--user-data-dir={profile}",
        "--virtual-time-budget=5000",
        "--dump-dom",
        f"{origin}/",
    ]

    with run_server(app, port):
        result = run_browser_command(command, tmp_path, "hostile-browser")

    assert result.returncode == 0, result.stderr
    assert 'data-karst-dashboard-ready="complete"' in result.stdout.lower()
    collector = TextCollector()
    collector.feed(result.stdout)
    rendered_text = "".join(collector.parts)
    assert xss_payload in rendered_text
    assert admin_payload in rendered_text
    assert 'data-karst-xss="1"' not in result.stdout.lower()
    assert admin_attempts == []


def test_dashboard_interactions_are_race_safe_and_code_dots_are_type_only(
    tmp_path: Path,
) -> None:
    browser = find_browser()
    if browser is None:
        pytest.skip("Chrome/Edge is unavailable for dashboard behavior proof")

    prelude = DASHBOARD_BEHAVIOR_PRELUDE
    scenario = r"""
        document.addEventListener('DOMContentLoaded', async () => {
            const pause = () => new Promise((resolve) => setTimeout(resolve, 0));
            const waitFor = async (predicate) => {
                for (let attempt = 0; attempt < 200; attempt += 1) {
                    if (predicate()) return true;
                    await new Promise((resolve) => setTimeout(resolve, 5));
                }
                return false;
            };
            const results = {};
            await waitFor(() => document.documentElement.dataset.karstDashboardReady === 'complete');

            results.filesTabAbsent = !document.querySelector('[data-detail-tab="files"]');
            const projectButton = [...document.querySelectorAll('.project-file-actions button')]
                .find((button) => button.textContent.includes('Nodes'));
            projectButton.click();
            await pause();
            results.projectActivated = !document.getElementById('drill-down-panel').hidden;
            results.nodesDefault = document.querySelector('[data-detail-tab="nodes"]').classList.contains('active')
                && document.getElementById('dd-content').textContent.includes('Class');
            document.querySelector('[data-detail-tab="telemetry"]').click();
            await pause();
            results.telemetryReachable = document.getElementById('dd-content').textContent.includes('query_symbol');
            document.querySelector('[data-detail-tab="commits"]').click();
            await pause();
            results.commitsReachable = document.getElementById('dd-content').textContent.includes('commit works');

            const canvas = document.getElementById('graph-canvas');
            canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1200, height: 620 });
            canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
            results.arrowSelectsFolder = document.getElementById('graph-detail').textContent.includes('press Enter');
            canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
            await pause();
            canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
            await pause();
            const successRace = globalThis.__graphRequests.slice(-2);
            successRace[1].resolve(globalThis.__fakeResponse(globalThis.__unscopedGraph));
            await pause();
            successRace[0].resolve(globalThis.__fakeResponse(globalThis.__focusedGraph));
            await pause();
            results.staleSuccessIgnored = !document.getElementById('graph-summary').textContent.includes('focused')
                && document.getElementById('graph-summary').textContent !== 'Graph unavailable';

            canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
            canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
            await pause();
            canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
            await pause();
            const failureRace = globalThis.__graphRequests.slice(-2);
            failureRace[1].resolve(globalThis.__fakeResponse(globalThis.__unscopedGraph));
            await pause();
            failureRace[0].reject(new Error('late focused failure'));
            await pause();
            results.staleFailureIgnored = document.getElementById('graph-summary').textContent !== 'Graph unavailable';

            canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
            canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
            await pause();
            const focusRequest = globalThis.__graphRequests.at(-1);
            focusRequest.resolve(globalThis.__fakeResponse(globalThis.__focusedGraph));
            await pause();
            results.focusStatus = document.getElementById('graph-summary').textContent.includes('focused folder context');
            results.focusFadesContext = globalThis.__graphContext.operations.some((operation) => operation.alpha === 0.24)
                && globalThis.__graphContext.operations.some((operation) => operation.alpha === 1);
            const dot = [...globalThis.__graphContext.operations].reverse()
                .find((operation) => operation.kind === 'fill' && operation.radius === 2.2);
            canvas.dispatchEvent(new MouseEvent('mousemove', { clientX: dot.x, clientY: dot.y, bubbles: true }));
            const detail = document.getElementById('graph-detail').textContent;
            results.dotTypeAnnounced = detail.toLowerCase().includes('class');
            const visibleBody = document.body.cloneNode(true);
            visibleBody.querySelectorAll('script').forEach((script) => script.remove());
            results.secretAbsent = !visibleBody.textContent.includes(SECRET_SYMBOL);

            canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
            await pause();
            globalThis.__graphRequests.at(-1).resolve(globalThis.__fakeResponse(globalThis.__unscopedGraph));
            await pause();
            results.escapeReset = !document.getElementById('graph-summary').textContent.includes('focused');

            const output = document.createElement('pre');
            output.id = 'behavior-results';
            output.textContent = JSON.stringify(results);
            document.body.appendChild(output);
        });
    """
    source, policy = instrumented_dashboard(prelude, scenario)
    result = dump_instrumented_dashboard(browser, tmp_path, source, policy)

    assert result.returncode == 0, result.stderr
    match = re.search(r'<pre id="behavior-results">(.*?)</pre>', result.stdout)
    assert match is not None, result.stdout
    outcomes = json.loads(match.group(1))
    assert outcomes == {
        "filesTabAbsent": True,
        "projectActivated": True,
        "nodesDefault": True,
        "telemetryReachable": True,
        "commitsReachable": True,
        "arrowSelectsFolder": True,
        "staleSuccessIgnored": True,
        "staleFailureIgnored": True,
        "focusStatus": True,
        "focusFadesContext": True,
        "dotTypeAnnounced": True,
        "secretAbsent": True,
        "escapeReset": True,
    }
