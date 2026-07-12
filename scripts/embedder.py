import time
from pathlib import Path
from src.database import Database

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
    
    for (node_id,) in nodes:
        cursor.execute("SELECT id FROM embeddings WHERE node_id = ?", (node_id,))
        if cursor.fetchone():
            continue
            
        time.sleep(0.1)
        dummy_vector = "[0.1, 0.2, 0.3]"
        cursor.execute("INSERT INTO embeddings (node_id, vector) VALUES (?, ?)", (node_id, dummy_vector))
        db.conn.commit()
        
    db.close()

if __name__ == "__main__":
    main()
