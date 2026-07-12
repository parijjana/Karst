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

_embed_model = None
def get_embed_model():
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer('BAAI/bge-small-en-v1.5')
    return _embed_model

def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    import numpy as np
    vec1 = np.array(v1)
    vec2 = np.array(v2)
    return float(np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2)))

def do_semantic_search(db: Any, project_id: int, query: str, limit: int = 5) -> tuple[str, float, int]:
    import json
    start_time = time.time()
    
    model = get_embed_model()
    query_vector = model.encode(query).tolist()
    
    cursor = db.conn.cursor()
    cursor.execute('''
        SELECT n.id, n.file_id, n.type, n.name, n.start_line, n.end_line, e.vector
        FROM embeddings e
        JOIN nodes n ON e.node_id = n.id
        WHERE n.project_id = ?
    ''', (project_id,))
    
    results = []
    for row in cursor.fetchall():
        node_id, file_id, ntype, name, start_line, end_line, vec_json = row
        vec = json.loads(vec_json)
        sim = cosine_similarity(query_vector, vec)
        results.append((sim, node_id, file_id, ntype, name, start_line, end_line))
        
    results.sort(key=lambda x: x[0], reverse=True)
    top_results = results[:limit]
    
    if not top_results:
        return f"No semantic matches found for '{query}'.", time.time() - start_time, 0
        
    output = [f"Top {len(top_results)} semantic matches for '{query}':"]
    for sim, node_id, file_id, ntype, name, start_line, end_line in top_results:
        cursor.execute("SELECT path FROM files WHERE id = ?", (file_id,))
        f_row = cursor.fetchone()
        filepath = f_row[0] if f_row else "Unknown file"
        output.append(f"- [{sim:.3f}] {ntype} '{name}' at {filepath}:{start_line}-{end_line}")
        
    response = "\n".join(output)
    return response, time.time() - start_time, 1500
