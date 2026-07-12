import time
import subprocess
from pathlib import Path

from src.database import Database

def main() -> None:
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    db_path = str(data_dir / "knowledge_graph.db")
    
    while True:
        db = Database(db_path)
        cursor = db.conn.cursor()
        cursor.execute("SELECT id, name, path FROM projects")
        projects = cursor.fetchall()
        db.close()
        
        for _, project_name, root_path in projects:
            start_time = time.time()
            status = "success"
            try:
                subprocess.run(
                    ["git", "fetch"],
                    cwd=root_path,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception as e:
                status = str(e)
                print(f"Failed to fetch {project_name}: {e}")
                
            latency_ms = (time.time() - start_time) * 1000
            import json
            db2 = Database(db_path)
            # Need to find project id for this name? 
            # projects is `(id, name, path)` so `for project_id, project_name, root_path in projects:`
            db2.log_telemetry(None, "service:git_poller", latency_ms, 0, json.dumps({"project": project_name, "status": status}))
            db2.close()
                
        time.sleep(60)

if __name__ == "__main__":
    main()
