import os
import sqlite3
import time
from pathlib import Path
from mcp.server.fastmcp import FastMCP

from src.database import Database
from src.parser import CodeParser
from src.git_logic import do_backfill_git_history
from src.query_logic import do_find_deps

# Ensure data directory exists
data_dir = Path("data")
data_dir.mkdir(exist_ok=True)
db_path = str(data_dir / "knowledge_graph.db")

mcp = FastMCP("Code Graph Server")

def get_db() -> Database:
    return Database(db_path)

def get_project_id(db: Database, project_name: str) -> int:
    cursor = db.conn.cursor()
    if not (row := cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,)).fetchone()):
        raise ValueError(f"Project '{project_name}' not found.")
    return row[0]

@mcp.tool()
def index_project(project_name: str, root_path: str) -> str:
    """Initialize a Database connection, walk root_path for code files, and index them."""
    start_time = time.time()
    db = get_db()
    parser = CodeParser()
    
    try:
        cursor = db.conn.cursor()
        if (row := cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,)).fetchone()):
            db.clear_project_data(row[0])
        project_id = db.add_project(project_name, root_path)
    except sqlite3.IntegrityError:
        db.close()
        return f"Failed to add project {project_name}"
        
    valid_exts = {".py", ".js", ".ts", ".dart", ".md"}
    # Explicitly ignore common non-hidden build/cache folders
    ignore_dirs = {"node_modules", "build", "dist", "__pycache__", "out", "target"}
    count = 0
    tokens_saved = 0
    for root, dirs, files in os.walk(root_path):
        # Modify dirs in-place: ignore exact matches and ALL hidden directories (like .git, .venv, .idea)
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith('.')]
        
        for file in files:
            ext = Path(file).suffix
            if ext in valid_exts:
                file_path = os.path.join(root, file)
                try:
                    tokens_saved += int(os.path.getsize(file_path) / 4)
                except Exception:
                    pass
                parser.parse_file(db, project_id, file_path)
                count += 1
                
    latency_ms = (time.time() - start_time) * 1000
    db.log_telemetry(project_id, "index_project", latency_ms, tokens_saved)
    db.close()
    return f"Indexed {count} files for project '{project_name}'."

@mcp.tool()
def update_graph(project_name: str, filepaths: list[str]) -> str:
    """Parse only the specified files and update their nodes in the DB."""
    start_time = time.time()
    db = get_db()
    parser = CodeParser()
    
    try:
        project_id = get_project_id(db, project_name)
    except ValueError as e:
        db.close()
        return str(e)
        
    cursor = db.conn.cursor()
    count = 0
    tokens_saved = 0
    for filepath in filepaths:
        cursor.execute("SELECT id FROM files WHERE project_id = ? AND path = ?", (project_id, filepath))
        row = cursor.fetchone()
        if row:
            cursor.execute("DELETE FROM files WHERE id = ?", (row[0],))
            db.conn.commit()
            
        try:
            tokens_saved += int(os.path.getsize(filepath) / 4)
        except Exception:
            pass
        parser.parse_file(db, project_id, filepath)
        count += 1
        
    latency_ms = (time.time() - start_time) * 1000
    db.log_telemetry(project_id, "update_graph", latency_ms, tokens_saved)
    db.close()
    return f"Updated {count} files for project '{project_name}'."

@mcp.tool()
def query_symbol(project_name: str, symbol_name: str) -> str:
    """Return definitions (file and line numbers) of a symbol."""
    start_time = time.time()
    db = get_db()
    try:
        project_id = get_project_id(db, project_name)
    except ValueError as e:
        db.close()
        return str(e)
        
    node = db.get_node_by_name(project_id, symbol_name)
    if not node:
        db.close()
        return f"Symbol '{symbol_name}' not found in project '{project_name}'."
        
    cursor = db.conn.cursor()
    cursor.execute("SELECT path FROM files WHERE id = ?", (node["file_id"],))
    file_row = cursor.fetchone()
    file_path = file_row[0] if file_row else "Unknown file"
    
    response = f"Symbol '{symbol_name}' ({node['type']}) defined in {file_path} from line {node['start_line']} to {node['end_line']}."
    
    try:
        raw_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        tokens_saved = max(0, int((raw_size / 4) - (len(response) / 4)))
    except Exception:
        tokens_saved = 0
        
    latency_ms = (time.time() - start_time) * 1000
    db.log_telemetry(project_id, "query_symbol", latency_ms, tokens_saved)
    db.close()
    return response

@mcp.tool()
def get_file_outline(project_name: str, filepath: str) -> str:
    """Return all classes/functions defined in a given file."""
    start_time = time.time()
    db = get_db()
    try:
        project_id = get_project_id(db, project_name)
    except ValueError as e:
        db.close()
        return str(e)
        
    cursor = db.conn.cursor()
    cursor.execute("SELECT id FROM files WHERE project_id = ? AND path = ?", (project_id, filepath))
    file_row = cursor.fetchone()
    if not file_row:
        db.close()
        return f"File '{filepath}' not found in project '{project_name}'."
        
    cursor.execute("SELECT name, type, start_line, end_line FROM nodes WHERE file_id = ? AND (type = 'class' OR type = 'function')", (file_row[0],))
    nodes = cursor.fetchall()
    
    if not nodes:
        db.close()
        return f"No classes or functions found in '{filepath}'."
        
    result = [f"Outline for {filepath}:"]
    for name, node_type, start, end in nodes:
        result.append(f"- {node_type} {name} (lines {start}-{end})")
        
    response = "\n".join(result)
    
    try:
        raw_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        tokens_saved = max(0, int((raw_size / 4) - (len(response) / 4)))
    except Exception:
        tokens_saved = 0
        
    latency_ms = (time.time() - start_time) * 1000
    db.log_telemetry(project_id, "get_file_outline", latency_ms, tokens_saved)
    db.close()
    return response

@mcp.tool()
def find_dependencies(project_name: str, symbol_name: str) -> str:
    """Return dependencies (edges where this symbol is source)."""
    db = get_db()
    try:
        project_id = get_project_id(db, project_name)
    except ValueError as e:
        db.close()
        return str(e)
        
    response, lat_s, tokens = do_find_deps(db, project_id, symbol_name, False)
    db.log_telemetry(project_id, "find_dependencies", lat_s * 1000, tokens)
    db.close()
    return response

@mcp.tool()
def find_dependents(project_name: str, symbol_name: str) -> str:
    """Return dependents (edges where this symbol is target)."""
    db = get_db()
    try:
        project_id = get_project_id(db, project_name)
    except ValueError as e:
        db.close()
        return str(e)
        
    response, lat_s, tokens = do_find_deps(db, project_id, symbol_name, True)
    db.log_telemetry(project_id, "find_dependents", lat_s * 1000, tokens)
    db.close()
    return response

@mcp.tool()
def log_commit(project_name: str, commit_hash: str, message: str, files_changed: list[dict]) -> str:
    """Log a git commit and the files impacted by it."""
    start_time = time.time()
    db = get_db()
    try:
        project_id = get_project_id(db, project_name)
    except ValueError as e:
        db.close()
        return str(e)
        
    db.log_commit(project_id, commit_hash, message, files_changed)
    
    latency_ms = (time.time() - start_time) * 1000
    db.log_telemetry(project_id, "log_commit", latency_ms, 0)
    db.close()
    return f"Logged commit {commit_hash} for project '{project_name}'."

@mcp.tool()
def backfill_git_history(project_name: str, limit: int = 100) -> str:
    """Traverse the git history of a project and ingest its commits into the graph."""
    db = get_db()
    try:
        project_id = get_project_id(db, project_name)
        cursor = db.conn.cursor()
        cursor.execute("SELECT path FROM projects WHERE id = ?", (project_id,))
        project_path = cursor.fetchone()[0]
    except ValueError as e:
        db.close()
        return str(e)

    res = do_backfill_git_history(db, project_id, project_name, project_path, limit)
    db.close()
    return res
if __name__ == "__main__":
    mcp.run()
