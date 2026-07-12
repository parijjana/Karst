import os
import time
from typing import Any

def do_find_deps(db: Any, project_id: int, symbol_name: str, is_dependent: bool) -> tuple[str, float, int]:
    start_time = time.time()
    node = db.get_node_by_name(project_id, symbol_name)
    if not node:
        return f"Symbol '{symbol_name}' not found.", time.time() - start_time, 0
        
    cursor = db.conn.cursor()
    if is_dependent:
        cursor.execute('''
            SELECT n.name, n.type, e.type
            FROM edges e
            JOIN nodes n ON e.source_id = n.id
            WHERE e.target_id = ?
        ''', (node["id"],))
        deps = cursor.fetchall()
        label = "Dependents"
    else:
        cursor.execute('''
            SELECT n.name, n.type, e.type
            FROM edges e
            JOIN nodes n ON e.target_id = n.id
            WHERE e.source_id = ?
        ''', (node["id"],))
        deps = cursor.fetchall()
        label = "Dependencies"
    
    if not deps:
        return f"No {label.lower()} found for '{symbol_name}'.", time.time() - start_time, 0
        
    result = [f"{label} for '{symbol_name}':"]
    for name, ntype, etype in deps:
        result.append(f"- {name} ({ntype}) [edge: {etype}]")
        
    response = "\n".join(result)
    
    try:
        cursor.execute("SELECT path FROM files WHERE id = ?", (node["file_id"],))
        file_path = cursor.fetchone()[0]
        raw_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        tokens_saved = max(0, int((raw_size / 4) - (len(response) / 4)))
    except Exception:
        tokens_saved = 0
        
    return response, time.time() - start_time, tokens_saved
