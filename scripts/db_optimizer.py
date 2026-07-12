from pathlib import Path
from src.database import Database

def main() -> None:
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    db_path = str(data_dir / "knowledge_graph.db")
    import time
    import json
    import os
    start_time = time.time()
    initial_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0

    db = Database(db_path)
    
    cursor = db.conn.cursor()
    # Prune telemetry older than 30 days
    cursor.execute("DELETE FROM telemetry WHERE timestamp < datetime('now', '-30 days')")
    db.conn.commit()
    
    # SQLite VACUUM cannot run inside an explicit transaction context
    db.conn.isolation_level = None
    db.conn.execute("VACUUM")
    db.conn.execute("ANALYZE")
    db.conn.isolation_level = ""
    
    db.close()
    
    final_size = os.path.getsize(db_path)
    space_freed_kb = max(0, initial_size - final_size) / 1024.0
    latency_ms = (time.time() - start_time) * 1000
    
    db2 = Database(db_path)
    db2.log_telemetry(None, "service:db_optimizer", latency_ms, 0, json.dumps({"space_freed_kb": space_freed_kb}))
    db2.close()

if __name__ == "__main__":
    main()
