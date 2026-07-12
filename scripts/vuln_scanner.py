from pathlib import Path
from src.database import Database

def scan_file(db: Database, project_id: int, file_id: int, file_path: str) -> None:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return
        
    vuln_patterns = ["shell=True", "os.system", "os.popen"]
    for i, line in enumerate(lines):
        for pattern in vuln_patterns:
            if pattern in line:
                db.add_node(project_id, file_id, "vulnerability", f"vuln: {pattern}", i + 1, i + 1)

def main() -> None:
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    db_path = str(data_dir / "knowledge_graph.db")
    db = Database(db_path)
    cursor = db.conn.cursor()
    
    cursor.execute("SELECT id, project_id, path FROM files WHERE path LIKE '%.py'")
    files = cursor.fetchall()
    
    for file_id, project_id, file_path in files:
        cursor.execute("DELETE FROM nodes WHERE file_id = ? AND type = 'vulnerability'", (file_id,))
        db.conn.commit()
        scan_file(db, project_id, file_id, file_path)
        
    db.close()

if __name__ == "__main__":
    main()
