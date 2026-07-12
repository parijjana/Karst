from pathlib import Path
from src.database import Database

def main() -> None:
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    db_path = str(data_dir / "knowledge_graph.db")
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

if __name__ == "__main__":
    main()
