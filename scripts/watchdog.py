from __future__ import annotations

import csv
import io
import os
import signal
import subprocess
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from src.mission_control_transition.runtime_store import RuntimeStore, create_runtime_store


COMMAND_TIMEOUT_SECONDS = 5.0
MAX_PID = (1 << 31) - 1
TERMINATION_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


@dataclass(frozen=True)
class TerminationResult:
    message: str
    unregister: bool


def _validate_target(pid: int, script_name: str) -> str | None:
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or pid > MAX_PID
    ):
        return f"Refused to terminate invalid PID {pid!r}."
    if pid == os.getpid():
        return f"Refused to terminate watchdog process PID {pid}."
    if not isinstance(script_name, str) or not script_name.strip():
        return f"Refused to terminate PID {pid} with an invalid process identity."
    return None


def process_exists(pid: int, timeout: float = COMMAND_TIMEOUT_SECONDS) -> bool:
    """Check liveness without using a command shell.

    The active-process schema has no process creation token or executable identity.
    This liveness check, PID validation, and the self-process guard are therefore the
    strongest safe checks this bounded watchdog can make. A durable supervisor must
    persist and compare an immutable process identity before terminating a reused PID.
    """
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            check=False,
            shell=False,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"process identity check timed out after {timeout:g}s") from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no details"
        raise RuntimeError(
            f"process identity check exited with code {completed.returncode}: {detail}"
        )

    rows = csv.reader(io.StringIO(completed.stdout))
    return any(len(row) >= 2 and row[1].strip() == str(pid) for row in rows)


def terminate_process(
    pid: int,
    script_name: str,
    command_timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> TerminationResult:
    validation_error = _validate_target(pid, script_name)
    if validation_error is not None:
        return TerminationResult(validation_error, unregister=True)

    try:
        exists = process_exists(pid, command_timeout)
    except (OSError, RuntimeError, OverflowError, ValueError) as exc:
        return TerminationResult(
            f"Failed to validate PID {pid} for {script_name}: {exc}",
            unregister=False,
        )

    if not exists:
        return TerminationResult(
            f"PID {pid} for {script_name} no longer exists.",
            unregister=True,
        )

    if os.name != "nt":
        try:
            os.kill(pid, TERMINATION_SIGNAL)
        except ProcessLookupError:
            return TerminationResult(
                f"PID {pid} for {script_name} exited before termination.",
                unregister=True,
            )
        except (OSError, OverflowError, ValueError) as exc:
            return TerminationResult(
                f"Failed to kill PID {pid} for {script_name}: {exc}",
                unregister=False,
            )
        return TerminationResult(f"Successfully killed PID {pid}.", unregister=True)

    command = ["taskkill", "/F", "/PID", str(pid)]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            shell=False,
            text=True,
            timeout=command_timeout,
        )
    except subprocess.TimeoutExpired:
        return TerminationResult(
            f"Failed to kill PID {pid}: taskkill timed out after {command_timeout:g}s.",
            unregister=False,
        )
    except OSError as exc:
        return TerminationResult(
            f"Failed to kill PID {pid}: could not start taskkill: {exc}",
            unregister=False,
        )

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no details"
        return TerminationResult(
            f"Failed to kill PID {pid}: taskkill exit code "
            f"{completed.returncode}: {detail}",
            unregister=False,
        )

    return TerminationResult(f"Successfully killed PID {pid}.", unregister=True)


def _process_stale_record(store: RuntimeStore, process: Mapping[str, Any]) -> None:
    pid = process["pid"]
    script_name = process["script_name"]
    last_status = process["last_status"]
    elapsed = process["elapsed_seconds"]

    print(
        f"ZOMBIE DETECTED: {script_name} (PID: {pid}) hung for "
        f"{elapsed:.1f}s. Last status: '{last_status}'"
    )

    result = terminate_process(pid, script_name)
    print(result.message)

    details = (
        f"Last Status: '{last_status}'. Hung for {elapsed:.1f}s. {result.message}"
    )
    try:
        store.record_event("watchdog_termination", pid, script_name, details)
    finally:
        if result.unregister:
            store.unregister_process(pid)


def process_stale_records(
    store: RuntimeStore,
    stale_processes: Iterable[Mapping[str, Any]],
) -> None:
    for process in stale_processes:
        try:
            _process_stale_record(store, process)
        except Exception as exc:
            print(f"Watchdog process error: {exc}")


def main() -> None:
    timeout_seconds = 60
    check_interval = 10

    print(f"Starting Watchdog Daemon (Timeout: {timeout_seconds}s)")

    while True:
        store: RuntimeStore | None = None
        try:
            store = create_runtime_store()
            stale_processes = store.get_stale_processes(timeout_seconds)
            process_stale_records(store, stale_processes)
        except Exception as exc:
            print(f"Watchdog error: {exc}")
        finally:
            if store is not None:
                store.close()

        time.sleep(check_interval)


if __name__ == "__main__":
    main()
