from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from src import git_logic


class RecordingDatabase:
    def __init__(self) -> None:
        self.commits: list[tuple[int, str, str, list[dict[str, str]]]] = []
        self.telemetry: list[tuple[Any, ...]] = []

    def log_commit(
        self,
        project_id: int,
        commit_hash: str,
        message: str,
        files: list[dict[str, str]],
    ) -> None:
        self.commits.append((project_id, commit_hash, message, files))

    def log_telemetry(self, *args: Any) -> None:
        self.telemetry.append(args)


def completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_backfill_uses_bounded_argv_and_parses_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(command: list[str], **kwargs: Any):
        calls.append((command, kwargs))
        if "rev-parse" in command:
            return completed(stdout="true\n")
        return completed(
            stdout=(
                "COMMIT|new|new message\nM\tnew.py\n\n"
                "COMMIT|old|old | message\nA\told.py\n"
            )
        )

    monkeypatch.setattr(git_logic.subprocess, "run", fake_run)
    database = RecordingDatabase()

    result = git_logic.do_backfill_git_history(
        database, 3, "demo", str(tmp_path), limit=2
    )

    assert result == "Backfilled 2 commits into the knowledge graph for project 'demo'."
    assert database.commits == [
        (3, "new", "new message", [{"status": "M", "path": "new.py"}]),
        (3, "old", "old | message", [{"status": "A", "path": "old.py"}]),
    ]
    assert calls[1][0] == [
        "git",
        "log",
        "-n",
        "2",
        "--name-status",
        "--pretty=format:COMMIT|%H|%s",
    ]
    assert all(call[1]["timeout"] == git_logic.GIT_TIMEOUT_SECONDS for call in calls)
    assert database.telemetry


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (subprocess.TimeoutExpired(["git"], 1), "Git operation timed out."),
        (FileNotFoundError(), "Git executable is unavailable."),
        (OSError("denied"), "Git operation could not be started."),
    ],
)
def test_backfill_reports_process_start_and_timeout_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
    expected: str,
) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise failure

    monkeypatch.setattr(git_logic.subprocess, "run", fail)

    result = git_logic.do_backfill_git_history(
        RecordingDatabase(), 1, "demo", str(tmp_path)
    )

    assert result == expected


def test_backfill_reports_non_repository_and_log_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = iter([completed(returncode=1, stderr="not a repo")])
    monkeypatch.setattr(
        git_logic.subprocess, "run", lambda *_args, **_kwargs: next(responses)
    )
    assert "does not appear" in git_logic.do_backfill_git_history(
        RecordingDatabase(), 1, "demo", str(tmp_path)
    )

    responses = iter(
        [completed(stdout="true"), completed(returncode=2, stderr="bad revision")]
    )
    monkeypatch.setattr(
        git_logic.subprocess, "run", lambda *_args, **_kwargs: next(responses)
    )
    assert (
        git_logic.do_backfill_git_history(RecordingDatabase(), 1, "demo", str(tmp_path))
        == "Git error: bad revision"
    )


@pytest.mark.parametrize("limit", [0, -1, 1001])
def test_backfill_rejects_unbounded_history_limit(tmp_path: Path, limit: int) -> None:
    assert (
        git_logic.do_backfill_git_history(
            RecordingDatabase(), 1, "demo", str(tmp_path), limit=limit
        )
        == "Git history limit must be between 1 and 1000."
    )
