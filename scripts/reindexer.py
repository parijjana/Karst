import os
import time
from pathlib import Path

from src.database import Database
from src.parser import CodeParser

def main() -> None:
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    db_path = str(data_dir / "knowledge_graph.db")
    db = Database(db_path)
    
    cursor = db.conn.cursor()
    cursor.execute("SELECT id, name, path FROM projects")
    projects = cursor.fetchall()
    
    parser = CodeParser()
    valid_exts = {".py", ".js", ".ts", ".dart", ".md"}
    ignore_dirs = {"node_modules", "build", "dist", "__pycache__", "out", "target"}
    
    for _, project_name, root_path in projects:
        start_time = time.time()
        
        cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
        if row := cursor.fetchone():
            db.clear_project_data(row[0])
            
        project_id = db.add_project(project_name, root_path)
        
        count = 0
        tokens_saved = 0
        
        for root, dirs, files in os.walk(root_path):
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
                    
        import json
        latency_ms = (time.time() - start_time) * 1000
        db.log_telemetry(project_id, "service:reindexer", latency_ms, count, json.dumps({"bytes_processed": tokens_saved * 4}))
        
    db.close()

if __name__ == "__main__":
    main()
