from pathlib import Path
from src.karst_core.database.database import Database

def scan_file(db: Database, project_id: int, file_id: int, file_path: str) -> int:
    vulns_found = 0
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return vulns_found
        
    vuln_patterns = ["shell=True", "os.system", "os.popen"]
    for i, line in enumerate(lines):
        for pattern in vuln_patterns:
            if pattern in line:
                db.add_node(project_id, file_id, "vulnerability", f"vuln: {pattern}", i + 1, i + 1)
                vulns_found += 1
    return vulns_found

def main() -> None:
    import time
    import json
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    db_path = str(data_dir / "knowledge_graph.db")
    start_time = time.time()
    
    db = Database(db_path)
    cursor = db.conn.cursor()
    
    cursor.execute("SELECT id, project_id, path FROM files WHERE path LIKE '%.py'")
    files = cursor.fetchall()
    
    total_vulns = 0
    for file_id, project_id, file_path in files:
        cursor.execute("DELETE FROM nodes WHERE file_id = ? AND type = 'vulnerability'", (file_id,))
        db.conn.commit()
        total_vulns += scan_file(db, project_id, file_id, file_path)
        
    latency_ms = (time.time() - start_time) * 1000
    db.log_telemetry(None, "service:vuln_scanner", latency_ms, total_vulns, json.dumps({"files_scanned": len(files)}))
    
    db.close()

if __name__ == "__main__":
    main()
