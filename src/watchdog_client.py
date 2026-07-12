import os
import contextlib
from src.database import Database

class WatchdogClient:
    def __init__(self, db_path: str, script_name: str):
        self.db_path = db_path
        self.script_name = script_name
        self.pid = os.getpid()
        self.db = None
        
    def __enter__(self):
        self.db = Database(self.db_path)
        self.db.register_process(self.pid, self.script_name, "Started")
        return self
        
    def update_progress(self, status: str):
        if self.db:
            self.db.update_process_heartbeat(self.pid, status)
            
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.db:
            self.db.unregister_process(self.pid)
            self.db.close()

@contextlib.contextmanager
def managed_process(script_name: str, db_path: str = "data/knowledge_graph.db"):
    """
    Context manager to wrap background scripts for watchdog monitoring.
    Usage:
        with managed_process("my_script") as wd:
            for item in items:
                wd.update_progress(f"Processing {item}")
    """
    client = WatchdogClient(db_path, script_name)
    with client:
        yield client
