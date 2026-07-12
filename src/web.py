import sqlite3
import os
from contextlib import contextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI(title="Code Graph Dashboard")

DB_PATH = "data/knowledge_graph.db"

@contextmanager
def get_db():
    uri = f"file:{os.path.abspath(DB_PATH)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Code Graph Dashboard</title>
    <style>
        :root {
            --bg-color: #0F172A;
            --panel-bg: #1E293B;
            --text-color: #E2E8F0;
            --accent-cyan: #06B6D4;
            --accent-violet: #8B5CF6;
            --border-color: #334155;
        }
        body { background-color: var(--bg-color); color: var(--text-color); font-family: 'Courier New', Courier, monospace; margin: 0; padding: 20px; }
        h1, h2, h3 { color: var(--accent-cyan); }
        .container { max-width: 1200px; margin: 0 auto; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .stat-card { background-color: var(--panel-bg); border: 1px solid var(--accent-violet); border-radius: 8px; padding: 20px; text-align: center; box-shadow: 0 4px 6px -1px rgba(139, 92, 246, 0.2); }
        .stat-card h3 { margin: 0 0 10px 0; font-size: 1.1em; color: var(--text-color); }
        .stat-card .value { font-size: 2em; font-weight: bold; color: var(--accent-cyan); }
        .panel { background-color: var(--panel-bg); border: 1px solid var(--border-color); border-radius: 8px; padding: 20px; margin-bottom: 30px; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid var(--border-color); }
        th { color: var(--accent-violet); }
        tr:hover { background-color: rgba(6, 182, 212, 0.1); }
        .clickable { cursor: pointer; color: var(--accent-cyan); text-decoration: underline; }
        #drill-down-content { margin-top: 20px; }
        .tabs { display: flex; gap: 10px; margin-bottom: 10px; }
        .tab { padding: 5px 10px; background-color: var(--bg-color); border: 1px solid var(--accent-cyan); color: var(--accent-cyan); cursor: pointer; border-radius: 4px; }
        .tab.active { background-color: var(--accent-cyan); color: var(--bg-color); }
    </style>
</head>
<body>
    <div class="container">
        <h1>Code Graph Telemetry</h1>
        
        <div class="stats-grid" id="stats-grid">
            <div class="stat-card"><h3>Total Projects</h3><div class="value" id="stat-projects">-</div></div>
            <div class="stat-card"><h3>Total Indexed</h3><div class="value" id="stat-indexed">-</div></div>
            <div class="stat-card"><h3>Queries Served</h3><div class="value" id="stat-queries">-</div></div>
            <div class="stat-card"><h3>Tokens Saved</h3><div class="value" id="stat-tokens">-</div></div>
        </div>

        <div class="panel">
            <h2>Projects</h2>
            <div id="projects-container">Loading...</div>
        </div>

        <div class="panel" id="drill-down-panel" style="display: none;">
            <h2>Project Drill-Down: <span id="dd-project-name"></span></h2>
            <div class="tabs">
                <div class="tab active" onclick="showTab('files')">Files</div>
                <div class="tab" onclick="showTab('nodes')">Nodes</div>
                <div class="tab" onclick="showTab('telemetry')">Telemetry</div>
            </div>
            <div id="dd-content"></div>
        </div>
    </div>

    <script>
        let currentProjectId = null;

        async function fetchJSON(url) {
            const res = await fetch(url);
            if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
            return await res.json();
        }

        async function loadDashboard() {
            try {
                const stats = await fetchJSON('/api/stats');
                document.getElementById('stat-projects').innerText = stats.total_projects;
                document.getElementById('stat-indexed').innerText = stats.total_nodes;
                document.getElementById('stat-queries').innerText = stats.queries_served;
                document.getElementById('stat-tokens').innerText = stats.tokens_saved;

                const projects = await fetchJSON('/api/projects');
                let pHtml = '<table><tr><th>ID</th><th>Name</th><th>Path</th></tr>';
                projects.forEach(p => {
                    pHtml += `<tr onclick="loadProject(${p.id}, '${p.name}')" class="clickable">
                                <td>${p.id}</td><td>${p.name}</td><td>${p.path}</td>
                              </tr>`;
                });
                pHtml += '</table>';
                document.getElementById('projects-container').innerHTML = pHtml;
            } catch (e) {
                console.error(e);
            }
        }

        async function loadProject(id, name) {
            currentProjectId = id;
            document.getElementById('dd-project-name').innerText = name;
            document.getElementById('drill-down-panel').style.display = 'block';
            showTab('files'); // default
        }

        async function showTab(tabName) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            event.currentTarget.classList.add('active');
            const contentDiv = document.getElementById('dd-content');
            contentDiv.innerHTML = 'Loading...';

            try {
                const data = await fetchJSON(`/api/projects/${currentProjectId}/${tabName}`);
                let html = '<table><tr>';
                if (data.length === 0) {
                    contentDiv.innerHTML = 'No data available.';
                    return;
                }
                // headers
                Object.keys(data[0]).forEach(k => { html += `<th>${k}</th>`; });
                html += '</tr>';
                // rows
                data.forEach(row => {
                    html += '<tr>';
                    Object.values(row).forEach(v => { html += `<td>${v}</td>`; });
                    html += '</tr>';
                });
                html += '</table>';
                contentDiv.innerHTML = html;
            } catch (e) {
                contentDiv.innerHTML = 'Error loading data.';
                console.error(e);
            }
        }

        window.onload = loadDashboard;
    </script>
</body>
</html>
"""

def table_exists(cursor, table_name: str) -> bool:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cursor.fetchone() is not None

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    return HTML_CONTENT

@app.get("/api/stats")
async def get_stats():
    if not os.path.exists(DB_PATH):
        return {"total_projects": 0, "total_nodes": 0, "queries_served": 0, "tokens_saved": 0}
        
    with get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) as c FROM projects")
        projects_count = cursor.fetchone()['c']
        
        cursor.execute("SELECT COUNT(*) as c FROM nodes")
        nodes_count = cursor.fetchone()['c']
        
        queries_served = 0
        tokens_saved = 0
        if table_exists(cursor, "telemetry"):
            cursor.execute("SELECT COUNT(*) as c, SUM(tokens_saved) as t FROM telemetry")
            row = cursor.fetchone()
            if row:
                queries_served = row['c'] or 0
                tokens_saved = row['t'] or 0
                
        return {
            "total_projects": projects_count,
            "total_nodes": nodes_count,
            "queries_served": queries_served,
            "tokens_saved": tokens_saved
        }

@app.get("/api/projects")
async def get_projects():
    if not os.path.exists(DB_PATH):
        return []
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, path FROM projects")
        return [dict(row) for row in cursor.fetchall()]

@app.get("/api/projects/{project_id}/files")
async def get_project_files(project_id: int):
    if not os.path.exists(DB_PATH):
        return []
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, path, hash FROM files WHERE project_id = ?", (project_id,))
        return [dict(row) for row in cursor.fetchall()]

@app.get("/api/projects/{project_id}/nodes")
async def get_project_nodes(project_id: int):
    if not os.path.exists(DB_PATH):
        return []
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, file_id, type, name, start_line, end_line FROM nodes WHERE project_id = ? LIMIT 500", (project_id,))
        return [dict(row) for row in cursor.fetchall()]

@app.get("/api/projects/{project_id}/telemetry")
async def get_project_telemetry(project_id: int):
    if not os.path.exists(DB_PATH):
        return []
    with get_db() as conn:
        cursor = conn.cursor()
        if not table_exists(cursor, "telemetry"):
            return []
        cursor.execute("SELECT id, tool_name, latency_ms, tokens_saved, timestamp FROM telemetry WHERE project_id = ? ORDER BY timestamp DESC LIMIT 500", (project_id,))
        return [dict(row) for row in cursor.fetchall()]

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
