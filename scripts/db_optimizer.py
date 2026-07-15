from __future__ import annotations

import json
import os
import time
from pathlib import Path

from src.karst_core.database.database import Database


def optimize_database(db_path: Path) -> None:
    db = Database(str(db_path))
    try:
        db.conn.execute(
            "DELETE FROM telemetry WHERE timestamp < datetime('now', '-30 days')"
        )
        # End the pruning transaction before VACUUM, which SQLite requires to run
        # outside an explicit transaction. The normal connection transaction mode
        # can remain unchanged.
        db.conn.commit()
        db.conn.execute("VACUUM")
        db.conn.execute("ANALYZE")
        db.conn.commit()
    except Exception:
        db.conn.rollback()
        raise
    finally:
        db.close()


def log_optimization(
    db_path: Path,
    latency_ms: float,
    space_freed_kb: float,
) -> None:
    db = Database(str(db_path))
    try:
        db.log_telemetry(
            None,
            "service:db_optimizer",
            latency_ms,
            0,
            json.dumps({"space_freed_kb": space_freed_kb}),
        )
    finally:
        db.close()


def main() -> None:
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    db_path = data_dir / "knowledge_graph.db"
    start_time = time.monotonic()
    initial_size = os.path.getsize(db_path) if db_path.exists() else 0

    optimize_database(db_path)

    final_size = os.path.getsize(db_path)
    space_freed_kb = max(0, initial_size - final_size) / 1024.0
    latency_ms = (time.monotonic() - start_time) * 1000
    log_optimization(db_path, latency_ms, space_freed_kb)


if __name__ == "__main__":
    main()
