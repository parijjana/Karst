import time
import subprocess
from typing import Any

def do_backfill_git_history(db: Any, project_id: int, project_name: str, project_path: str, limit: int = 100) -> str:
    start_time = time.time()
    try:
        subprocess.check_call(["git", "rev-parse", "--is-inside-work-tree"], cwd=project_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return f"Project '{project_name}' does not appear to be inside a git repository."

    try:
        log_output = subprocess.check_output(
            ["git", "log", f"-n {limit}", "--name-status", "--pretty=format:COMMIT|%H|%s"],
            cwd=project_path,
            text=True
        )
    except subprocess.CalledProcessError as e:
        return f"Git error: {str(e)}"
        
    lines = log_output.strip().split("\n")
    commits_added = 0
    current_hash = None
    current_msg = None
    current_files: list[dict[str, str]] = []

    def flush_commit() -> None:
        nonlocal commits_added
        if current_hash:
            db.log_commit(project_id, current_hash, current_msg, current_files)
            commits_added += 1

    for line in lines:
        if not line.strip():
            continue
        if line.startswith("COMMIT|"):
            flush_commit()
            parts = line.split("|", 2)
            current_hash = parts[1]
            current_msg = parts[2] if len(parts) > 2 else ""
            current_files = []
        else:
            parts = line.split("\t")
            if len(parts) >= 2:
                current_files.append({"status": parts[0], "path": parts[1]})
                
    flush_commit()

    latency_ms = (time.time() - start_time) * 1000
    db.log_telemetry(project_id, "backfill_git_history", latency_ms, 0)
    return f"Backfilled {commits_added} commits into the knowledge graph for project '{project_name}'."
