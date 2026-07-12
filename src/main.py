import os
import sqlite3
from pathlib import Path
from mcp.server.fastmcp import FastMCP

from src.database import Database
from src.parser import CodeParser

# Ensure data directory exists
data_dir = Path("data")
data_dir.mkdir(exist_ok=True)
db_path = str(data_dir / "knowledge_graph.db")

mcp = FastMCP("Code Graph Server")

def get_db() -> Database:
    return Database(db_path)

def get_project_id(db: Database, project_name: str) -> int:
    cursor = db.conn.cursor()
    cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"Project '{project_name}' not found.")
    return row[0]

@mcp.tool()
def index_project(project_name: str, root_path: str) -> str:
    """Initialize a Database connection, walk root_path for code files, and index them."""
    db = get_db()
    parser = CodeParser()
    
    try:
        cursor = db.conn.cursor()
        cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
        row = cursor.fetchone()
        if row:
            db.clear_project_data(row[0])
            
        project_id = db.add_project(project_name, root_path)
    except sqlite3.IntegrityError:
        db.close()
        return f"Failed to add project {project_name}"
        
    valid_exts = {".py", ".js", ".ts", ".dart"}
    ignore_dirs = {".git", ".venv", "node_modules", "build", "dist", ".uv-cache", "__pycache__"}
    count = 0
    for root, dirs, files in os.walk(root_path):
        # Modify dirs in-place to prevent os.walk from visiting ignored directories
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        
        for file in files:
            ext = Path(file).suffix
            if ext in valid_exts:
                file_path = os.path.join(root, file)
                parser.parse_file(db, project_id, file_path)
                count += 1
                
    db.close()
    return f"Indexed {count} files for project '{project_name}'."

@mcp.tool()
def update_graph(project_name: str, filepaths: list[str]) -> str:
    """Parse only the specified files and update their nodes in the DB."""
    db = get_db()
    parser = CodeParser()
    
    try:
        project_id = get_project_id(db, project_name)
    except ValueError as e:
        db.close()
        return str(e)
        
    cursor = db.conn.cursor()
    count = 0
    for filepath in filepaths:
        cursor.execute("SELECT id FROM files WHERE project_id = ? AND path = ?", (project_id, filepath))
        row = cursor.fetchone()
        if row:
            cursor.execute("DELETE FROM files WHERE id = ?", (row[0],))
            db.conn.commit()
            
        parser.parse_file(db, project_id, filepath)
        count += 1
        
    db.close()
    return f"Updated {count} files for project '{project_name}'."

@mcp.tool()
def query_symbol(project_name: str, symbol_name: str) -> str:
    """Return definitions (file and line numbers) of a symbol."""
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
    
    db.close()
    return f"Symbol '{symbol_name}' ({node['type']}) defined in {file_path} from line {node['start_line']} to {node['end_line']}."

@mcp.tool()
def get_file_outline(project_name: str, filepath: str) -> str:
    """Return all classes/functions defined in a given file."""
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
    db.close()
    
    if not nodes:
        return f"No classes or functions found in '{filepath}'."
        
    result = [f"Outline for {filepath}:"]
    for name, node_type, start, end in nodes:
        result.append(f"- {node_type} {name} (lines {start}-{end})")
        
    return "\n".join(result)

@mcp.tool()
def find_dependencies(project_name: str, symbol_name: str) -> str:
    """Return dependencies (edges where this symbol is source)."""
    db = get_db()
    try:
        project_id = get_project_id(db, project_name)
    except ValueError as e:
        db.close()
        return str(e)
        
    node = db.get_node_by_name(project_id, symbol_name)
    if not node:
        db.close()
        return f"Symbol '{symbol_name}' not found."
        
    cursor = db.conn.cursor()
    cursor.execute('''
        SELECT n.name, n.type, e.type
        FROM edges e
        JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ?
    ''', (node["id"],))
    deps = cursor.fetchall()
    db.close()
    
    if not deps:
        return f"No dependencies found for '{symbol_name}'."
        
    result = [f"Dependencies for '{symbol_name}':"]
    for name, ntype, etype in deps:
        result.append(f"- {name} ({ntype}) [edge: {etype}]")
    return "\n".join(result)

@mcp.tool()
def find_dependents(project_name: str, symbol_name: str) -> str:
    """Return dependents (edges where this symbol is target)."""
    db = get_db()
    try:
        project_id = get_project_id(db, project_name)
    except ValueError as e:
        db.close()
        return str(e)
        
    node = db.get_node_by_name(project_id, symbol_name)
    if not node:
        db.close()
        return f"Symbol '{symbol_name}' not found."
        
    cursor = db.conn.cursor()
    cursor.execute('''
        SELECT n.name, n.type, e.type
        FROM edges e
        JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ?
    ''', (node["id"],))
    deps = cursor.fetchall()
    db.close()
    
    if not deps:
        return f"No dependents found for '{symbol_name}'."
        
    result = [f"Dependents for '{symbol_name}':"]
    for name, ntype, etype in deps:
        result.append(f"- {name} ({ntype}) [edge: {etype}]")
    return "\n".join(result)

if __name__ == "__main__":
    mcp.run()
