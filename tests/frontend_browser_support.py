from __future__ import annotations

import base64
import hashlib
import re
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
from fastapi import FastAPI
from fastapi.responses import HTMLResponse


INDEX_PATH = Path(__file__).parents[1] / "src" / "index.html"

DASHBOARD_BEHAVIOR_PRELUDE = r"""
    const SECRET_SYMBOL = 'SecretSymbolMustNeverRender';
    const unscopedGraph = {
        nodes: [
            { id: 'karst', type: 'karst', weight: 1 },
            { id: 'project_1', type: 'project', weight: 1, ancestor_ids: ['karst'], detail: { project_id: 1, name: 'demo' } },
            { id: 'folder_1', type: 'folder', weight: 1, parent_id: 'project_1', ancestor_ids: ['karst', 'project_1'], detail: { path: 'pkg', name: 'pkg' } },
            { id: 'file_1', type: 'file', weight: 1, parent_id: 'folder_1', ancestor_ids: ['karst', 'project_1', 'folder_1'], detail: { path: 'pkg/demo.py', name: 'demo.py' } },
            { id: 'dot_1', type: 'code_dot', weight: 1, parent_id: 'file_1', name: SECRET_SYMBOL }
        ],
        links: [
            { source: 'karst', target: 'project_1', type: 'structural' },
            { source: 'project_1', target: 'folder_1', type: 'structural' },
            { source: 'folder_1', target: 'file_1', type: 'structural' },
            { source: 'file_1', target: 'dot_1', type: 'code_node', node_type: 'class' }
        ]
    };
    const focusedGraph = {
        ...unscopedGraph,
        selected_folder_id: 'folder_1',
        nodes: unscopedGraph.nodes.map((node) => ({
            ...node,
            focus_state: ['folder_1', 'file_1', 'dot_1'].includes(node.id) ? 'focus' : 'context'
        }))
    };
    const projects = [{
        id: 1,
        name: 'demo',
        tracked_file_count: 1,
        nonblank_loc_total: 12,
        untracked_file_count: 0,
        untracked_folder_count: 0,
        discovered_not_indexed_count: 0,
        node_counts_by_type: { class: 1 }
    }];
    const response = (payload, status = 200) => ({
        ok: status >= 200 && status < 300,
        status,
        json: async () => structuredClone(payload)
    });
    globalThis.__fakeResponse = response;
    globalThis.__unscopedGraph = unscopedGraph;
    globalThis.__focusedGraph = focusedGraph;
    globalThis.__graphRequests = [];
    let initialGraphLoaded = false;
    globalThis.fetch = (url, options = {}) => {
        const path = String(url);
        if (path.startsWith('/api/graph')) {
            if (!initialGraphLoaded) {
                initialGraphLoaded = true;
                return Promise.resolve(response(unscopedGraph));
            }
            return new Promise((resolve, reject) => {
                const request = { path, resolve, reject, aborted: false };
                const signal = options.signal;
                if (signal) {
                    signal.addEventListener('abort', () => {
                        request.aborted = true;
                        reject(new DOMException('Aborted', 'AbortError'));
                    }, { once: true });
                }
                globalThis.__graphRequests.push(request);
            });
        }
        if (path.startsWith('/api/projects/1/telemetry')) {
            return Promise.resolve(response([{ operation: 'query_symbol', latency_ms: 2 }]));
        }
        if (path.startsWith('/api/projects/1/commits')) {
            return Promise.resolve(response([{ commit_hash: 'abc', message: 'commit works' }]));
        }
        if (path.startsWith('/api/projects')) return Promise.resolve(response(projects));
        if (path.startsWith('/api/stats')) return Promise.resolve(response({ total_projects: 1, total_nodes: 1, queries_served: 0, tokens_saved: 0 }));
        if (path.startsWith('/api/telemetry')) return Promise.resolve(response([]));
        if (path.startsWith('/api/services/metrics')) return Promise.resolve(response([]));
        if (path.startsWith('/api/admin/session/csrf')) return Promise.resolve(response({}, 401));
        return Promise.resolve(response([]));
    };
    const contexts = new WeakMap();
    HTMLCanvasElement.prototype.getContext = function () {
        if (contexts.has(this)) return contexts.get(this);
        const context = {
            operations: [],
            globalAlpha: 1,
            fillStyle: '',
            strokeStyle: '',
            lineWidth: 1,
            lastArc: null,
            clearRect() { this.operations = []; },
            beginPath() { this.lastArc = null; },
            arc(x, y, radius) { this.lastArc = { x, y, radius }; },
            fill() { if (this.lastArc) this.operations.push({ kind: 'fill', alpha: this.globalAlpha, ...this.lastArc }); },
            stroke() { if (this.lastArc) this.operations.push({ kind: 'stroke', alpha: this.globalAlpha, ...this.lastArc }); },
            moveTo() {},
            lineTo() {}
        };
        contexts.set(this, context);
        globalThis.__graphContext = context;
        return context;
    };
"""


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


def instrumented_dashboard(prelude: str, scenario: str) -> tuple[str, str]:
    source = INDEX_PATH.read_text(encoding="utf-8")
    source = source.replace("    <script>", f"    <script>{prelude}</script>\n    <script>", 1)
    source = source.replace("</body>", f"    <script>{scenario}</script>\n</body>", 1)
    scripts = re.findall(r"<script>(.*?)</script>", source, flags=re.DOTALL)
    hashes = " ".join(
        "'sha256-"
        + base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode(
            "ascii"
        )
        + "'"
        for script in scripts
    )
    policy_match = re.search(
        r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', source
    )
    assert policy_match is not None
    policy = re.sub(r"script-src [^;]+", f"script-src {hashes}", policy_match.group(1))
    source = source.replace(policy_match.group(1), policy, 1)
    return source, policy


def run_browser_command(
    command: list[str], tmp_path: Path, output_stem: str
) -> subprocess.CompletedProcess[str]:
    stdout_path = tmp_path / f"{output_stem}.stdout"
    stderr_path = tmp_path / f"{output_stem}.stderr"
    with stdout_path.open("w", encoding="utf-8") as stdout_file, (
        stderr_path.open("w", encoding="utf-8")
    ) as stderr_file:
        process = subprocess.Popen(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=stdout_file,
            stderr=stderr_file,
        )
        try:
            returncode = process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            pytest.fail(f"{output_stem} browser did not exit within 20 seconds")
    return subprocess.CompletedProcess(
        command,
        returncode,
        stdout_path.read_text(encoding="utf-8", errors="replace"),
        stderr_path.read_text(encoding="utf-8", errors="replace"),
    )


def dump_instrumented_dashboard(
    browser: str, tmp_path: Path, source: str, policy: str
) -> subprocess.CompletedProcess[str]:
    port = reserve_port()
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        return HTMLResponse(source, headers={"Content-Security-Policy": policy})

    profile = tmp_path / "instrumented-browser-profile"
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
        "--virtual-time-budget=10000",
        "--dump-dom",
        f"http://127.0.0.1:{port}/",
    ]
    with run_server(app, port):
        return run_browser_command(command, tmp_path, "instrumented-browser")


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
        thread.join(timeout=3)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=2)
