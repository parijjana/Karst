import os
import contextlib
from pathlib import Path

from src.mission_control_transition.runtime_store import RuntimeStore, create_runtime_store


class WatchdogClient:
    def __init__(self, runtime_db_path: str | Path | None, script_name: str):
        self.runtime_db_path = runtime_db_path
        self.script_name = script_name
        self.pid = os.getpid()
        self.store: RuntimeStore | None = None

    def __enter__(self):
        self.store = create_runtime_store(self.runtime_db_path)
        self.store.register_process(self.pid, self.script_name, "Started")
        return self

    def update_progress(self, status: str):
        if self.store:
            self.store.update_process_heartbeat(self.pid, status)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.store:
            self.store.unregister_process(self.pid)
            self.store.close()


@contextlib.contextmanager
def managed_process(script_name: str, runtime_db_path: str | Path | None = None):
    """
    Context manager to wrap background scripts for watchdog monitoring.
    Usage:
        with managed_process("my_script") as wd:
            for item in items:
                wd.update_progress(f"Processing {item}")
    """
    client = WatchdogClient(runtime_db_path, script_name)
    with client:
        yield client
