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

def table_exists(cursor, table_name: str) -> bool:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cursor.fetchone() is not None

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()

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

@app.get("/api/projects/{project_id}/commits")
async def get_project_commits(project_id: int):
    if not os.path.exists(DB_PATH):
        return []
    with get_db() as conn:
        cursor = conn.cursor()
        if not table_exists(cursor, "commits"):
            return []
        # Get commits and group their files
        cursor.execute("""
            SELECT c.id, c.commit_hash, c.message, c.timestamp, 
                   GROUP_CONCAT(cf.status || ':' || cf.file_path, ', ') as files_changed
            FROM commits c
            LEFT JOIN commit_files cf ON c.id = cf.commit_id
            WHERE c.project_id = ?
            GROUP BY c.id
            ORDER BY c.timestamp DESC LIMIT 100
        """, (project_id,))
        return [dict(row) for row in cursor.fetchall()]

@app.get("/api/graph")
async def get_graph(project_id: int = None):
    if not os.path.exists(DB_PATH):
        return {"nodes": [], "links": []}
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        nodes = []
        links = []
        
        # Build query constraints
        where_clause = "WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()
        
        # 1. Fetch files as nodes
        cursor.execute(f"SELECT id, path, project_id FROM files {where_clause}", params)
        for row in cursor.fetchall():
            nodes.append({
                "id": f"file_{row['id']}",
                "name": row['path'].split('/')[-1],
                "type": "file",
                "group": row['project_id']
            })
            
        # 2. Fetch AST nodes
        cursor.execute(f"SELECT id, file_id, type, name, project_id FROM nodes {where_clause} LIMIT 2000", params)
        for row in cursor.fetchall():
            nodes.append({
                "id": f"node_{row['id']}",
                "name": row['name'],
                "type": row['type'],
                "group": row['project_id']
            })
            # Link node to its file
            links.append({
                "source": f"node_{row['id']}",
                "target": f"file_{row['file_id']}"
            })
            
        # 3. Fetch explicit edges (dependencies)
        cursor.execute(f"SELECT source_id, target_id FROM edges {where_clause} LIMIT 2000", params)
        for row in cursor.fetchall():
            links.append({
                "source": f"node_{row['source_id']}",
                "target": f"node_{row['target_id']}"
            })
            
        return {"nodes": nodes, "links": links}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8082)
