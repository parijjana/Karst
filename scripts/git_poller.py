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
            try:
                subprocess.run(
                    ["git", "fetch"],
                    cwd=root_path,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception as e:
                print(f"Failed to fetch {project_name}: {e}")
                
        time.sleep(60)

if __name__ == "__main__":
    main()
