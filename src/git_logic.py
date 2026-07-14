from __future__ import annotations

import subprocess
import time
from typing import Any


GIT_TIMEOUT_SECONDS = 15.0
MAX_HISTORY_LIMIT = 1000


def _run_git(
    arguments: list[str], project_path: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=project_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=GIT_TIMEOUT_SECONDS,
    )


def do_backfill_git_history(
    db: Any,
    project_id: int,
    project_name: str,
    project_path: str,
    limit: int = 100,
) -> str:
    if not 1 <= limit <= MAX_HISTORY_LIMIT:
        return f"Git history limit must be between 1 and {MAX_HISTORY_LIMIT}."

    start_time = time.monotonic()
    try:
        repository_check = _run_git(
            ["rev-parse", "--is-inside-work-tree"], project_path
        )
        if repository_check.returncode != 0:
            return (
                f"Project '{project_name}' does not appear to be inside "
                "a git repository."
            )
        history = _run_git(
            [
                "log",
                "-n",
                str(limit),
                "--name-status",
                "--pretty=format:COMMIT|%H|%s",
            ],
            project_path,
        )
    except subprocess.TimeoutExpired:
        return "Git operation timed out."
    except FileNotFoundError:
        return "Git executable is unavailable."
    except OSError:
        return "Git operation could not be started."

    if history.returncode != 0:
        detail = history.stderr.strip() or f"exit code {history.returncode}"
        return f"Git error: {detail}"

    commits_added = _store_commits(db, project_id, history.stdout)
    latency_ms = (time.monotonic() - start_time) * 1000
    db.log_telemetry(project_id, "backfill_git_history", latency_ms, 0)
    return (
        f"Backfilled {commits_added} commits into the knowledge graph "
        f"for project '{project_name}'."
    )


def _store_commits(db: Any, project_id: int, output: str) -> int:
    commits_added = 0
    current_hash: str | None = None
    current_message = ""
    current_files: list[dict[str, str]] = []

    def flush_commit() -> None:
        nonlocal commits_added
        if current_hash is None:
            return
        db.log_commit(
            project_id,
            current_hash,
            current_message,
            list(current_files),
        )
        commits_added += 1

    for line in output.splitlines():
        if not line.strip():
            continue
        if line.startswith("COMMIT|"):
            flush_commit()
            parts = line.split("|", 2)
            current_hash = parts[1] if len(parts) > 1 else ""
            current_message = parts[2] if len(parts) > 2 else ""
            current_files = []
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and current_hash is not None:
            current_files.append({"status": parts[0], "path": parts[-1]})

    flush_commit()
    return commits_added
