import json
from pathlib import Path
from sentence_transformers import SentenceTransformer
from src.database import Database

def get_node_text(db: Database, node_id: int) -> str:
    cursor = db.conn.cursor()
    cursor.execute("""
        SELECT n.type, n.name, n.start_line, n.end_line, f.path
        FROM nodes n
        JOIN files f ON n.file_id = f.id
        WHERE n.id = ?
    """, (node_id,))
    row = cursor.fetchone()
    if not row:
        return ""
    
    type_, name, start_line, end_line, file_path = row
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        snippet = "".join(lines[start_line-1:end_line])
        return f"{type_} {name}\n{snippet}"
    except Exception:
        return f"{type_} {name}"

def main() -> None:
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    db_path = str(data_dir / "knowledge_graph.db")
    db = Database(db_path)
    cursor = db.conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS embeddings (
            id INTEGER PRIMARY KEY,
            node_id INTEGER,
            vector TEXT,
            FOREIGN KEY(node_id) REFERENCES nodes(id) ON DELETE CASCADE
        )
    ''')
    db.conn.commit()
    
    cursor.execute("SELECT id FROM nodes")
    nodes = cursor.fetchall()
    
    nodes_to_embed = []
    for (node_id,) in nodes:
        cursor.execute("SELECT id FROM embeddings WHERE node_id = ?", (node_id,))
        if not cursor.fetchone():
            nodes_to_embed.append(node_id)
            
    if not nodes_to_embed:
        db.close()
        return
        
    print(f"Loading BAAI/bge-small-en-v1.5 model... (Found {len(nodes_to_embed)} nodes to embed)")
    model = SentenceTransformer('BAAI/bge-small-en-v1.5')
    print("Model loaded. Starting embedding process...")
    

    for node_id in nodes_to_embed:
        text = get_node_text(db, node_id)
        if not text:
            continue
            
        vector = model.encode(text).tolist()
        vector_json = json.dumps(vector)
        
        cursor.execute("INSERT INTO embeddings (node_id, vector) VALUES (?, ?)", (node_id, vector_json))
        db.conn.commit()
        
        pass
        
    # Optional: Log telemetry for embedder
    # Assuming the first node's project_id for simplicity, since it's a batch script, this might be cross-project.
    # We can skip telemetry here or log it. Let's just log it if we have a project.
    
    db.close()
    print("Embedding complete.")

if __name__ == "__main__":
    main()
