from __future__ import annotations

import subprocess
from typing import Any, cast

import pytest

from scripts import watchdog


def test_terminate_process_rejects_invalid_and_watchdog_pids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(watchdog.os, "getpid", lambda: 41)

    invalid = watchdog.terminate_process(0, "worker")
    own_process = watchdog.terminate_process(41, "worker")

    assert invalid.unregister is True
    assert "invalid PID" in invalid.message
    assert own_process.unregister is True
    assert "watchdog process" in own_process.message


def test_windows_termination_uses_argv_without_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="SUCCESS", stderr="")

    monkeypatch.setattr(watchdog.os, "name", "nt")
    monkeypatch.setattr(watchdog, "process_exists", lambda pid, timeout: True)
    monkeypatch.setattr(watchdog.subprocess, "run", fake_run)

    result = watchdog.terminate_process(321, "worker", command_timeout=2.5)

    assert result.unregister is True
    assert "Successfully killed PID 321" in result.message
    assert calls == [
        (
            ["taskkill", "/F", "/PID", "321"],
            {
                "capture_output": True,
                "check": False,
                "shell": False,
                "text": True,
                "timeout": 2.5,
            },
        )
    ]


def test_windows_termination_reports_timeout_without_unregistering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def time_out(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeout = kwargs["timeout"]
        assert isinstance(timeout, (int, float))
        raise subprocess.TimeoutExpired(command, float(timeout))

    monkeypatch.setattr(watchdog.os, "name", "nt")
    monkeypatch.setattr(watchdog, "process_exists", lambda pid, timeout: True)
    monkeypatch.setattr(watchdog.subprocess, "run", time_out)

    result = watchdog.terminate_process(321, "worker", command_timeout=1.0)

    assert result.unregister is False
    assert "timed out" in result.message


def test_windows_termination_checks_nonzero_exit_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 5, stdout="", stderr="Access denied")

    monkeypatch.setattr(watchdog.os, "name", "nt")
    monkeypatch.setattr(watchdog, "process_exists", lambda pid, timeout: True)
    monkeypatch.setattr(watchdog.subprocess, "run", fail)

    result = watchdog.terminate_process(321, "worker")

    assert result.unregister is False
    assert "exit code 5" in result.message
    assert "Access denied" in result.message


def test_non_windows_termination_uses_os_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(watchdog.os, "name", "posix")
    monkeypatch.setattr(watchdog, "process_exists", lambda pid, timeout: True)
    monkeypatch.setattr(watchdog.os, "kill", lambda pid, sig: calls.append((pid, sig)))

    result = watchdog.terminate_process(321, "worker")

    assert result.unregister is True
    assert calls == [(321, watchdog.TERMINATION_SIGNAL)]


def test_missing_process_is_not_killed_and_stale_registration_is_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(watchdog, "process_exists", lambda pid, timeout: False)
    monkeypatch.setattr(
        watchdog.os,
        "kill",
        lambda pid, sig: pytest.fail("missing process must not be signalled"),
    )

    result = watchdog.terminate_process(321, "worker")

    assert result.unregister is True
    assert "no longer exists" in result.message


def test_termination_rejects_pid_beyond_platform_safe_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        watchdog,
        "process_exists",
        lambda pid, timeout: pytest.fail("out-of-range PID must not be inspected"),
    )

    result = watchdog.terminate_process(watchdog.MAX_PID + 1, "worker")

    assert result.unregister is True
    assert "invalid PID" in result.message


def test_termination_contains_pid_api_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def overflow(pid: int, timeout: float) -> bool:
        raise OverflowError("Python int too large to convert to C pid_t")

    monkeypatch.setattr(watchdog, "process_exists", overflow)

    result = watchdog.terminate_process(321, "worker")

    assert result.unregister is False
    assert "Failed to validate PID 321" in result.message


def test_process_exists_uses_signal_zero_on_non_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(watchdog.os, "name", "posix")
    monkeypatch.setattr(watchdog.os, "kill", lambda pid, sig: calls.append((pid, sig)))

    assert watchdog.process_exists(123, timeout=1.0) is True
    assert calls == [(123, 0)]


def test_process_exists_checks_exact_windows_tasklist_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='"python.exe","1234","Console","1","10,000 K"\n',
            stderr="",
        )

    monkeypatch.setattr(watchdog.os, "name", "nt")
    monkeypatch.setattr(watchdog.subprocess, "run", fake_run)

    assert watchdog.process_exists(123, timeout=3.0) is False
    assert calls == [
        (
            ["tasklist", "/FI", "PID eq 123", "/FO", "CSV", "/NH"],
            {
                "capture_output": True,
                "check": False,
                "shell": False,
                "text": True,
                "timeout": 3.0,
            },
        )
    ]


def test_terminal_cleanup_survives_event_failure_and_next_record_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EventFailingStore:
        def __init__(self) -> None:
            self.event_calls = 0
            self.unregistered: list[int] = []

        def record_event(self, *args: Any, **kwargs: Any) -> None:
            self.event_calls += 1
            if self.event_calls == 1:
                raise RuntimeError("runtime event store unavailable")

        def unregister_process(self, pid: int) -> None:
            self.unregistered.append(pid)

    terminated: list[int] = []

    def terminate(
        pid: int,
        script_name: str,
        command_timeout: float = watchdog.COMMAND_TIMEOUT_SECONDS,
    ) -> watchdog.TerminationResult:
        terminated.append(pid)
        return watchdog.TerminationResult(f"terminated {script_name}", unregister=True)

    store = EventFailingStore()
    stale_processes = [
        {
            "pid": 101,
            "script_name": "first",
            "last_status": "hung",
            "elapsed_seconds": 61.0,
        },
        {
            "pid": 102,
            "script_name": "second",
            "last_status": "hung",
            "elapsed_seconds": 62.0,
        },
    ]
    monkeypatch.setattr(watchdog, "terminate_process", terminate)

    watchdog.process_stale_records(cast(Any, store), stale_processes)

    assert terminated == [101, 102]
    assert store.unregistered == [101, 102]
    assert store.event_calls == 2
