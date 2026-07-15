from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from functools import partial
from typing import BinaryIO

from src.settings import PROJECT_ROOT


ALLOWED_SCRIPTS = {
    "watchdog": ["uv", "run", "python", "-m", "scripts.watchdog"],
    "reindexer": ["uv", "run", "python", "-m", "scripts.reindexer"],
    "git_poller": ["uv", "run", "python", "-m", "scripts.git_poller"],
    "db_optimizer": ["uv", "run", "python", "-m", "scripts.db_optimizer"],
    "vuln_scanner": ["uv", "run", "python", "-m", "scripts.vuln_scanner"],
    "embedder": ["uv", "run", "python", "-m", "scripts.embedder"],
}
LOG_DIR = PROJECT_ROOT / "logs"
STOP_TIMEOUT_SECONDS = 5.0
POSIX_TERMINATE_SIGNAL = getattr(signal, "SIGTERM", 15)
POSIX_KILL_SIGNAL = getattr(signal, "SIGKILL", 9)

# The public process registry remains compatible with existing callers. Log
# ownership is tracked separately so every terminal path can close its handle.
active_processes: dict[str, asyncio.subprocess.Process] = {}
_log_handles: dict[str, BinaryIO] = {}
_watch_tasks: dict[str, asyncio.Task[None]] = {}
_registry_lock = asyncio.Lock()


def _validate_script_name(script_name: str) -> list[str]:
    try:
        return ALLOWED_SCRIPTS[script_name]
    except KeyError:
        raise ValueError(
            f"Script '{script_name}' is not in the allowed registry."
        ) from None


def _open_log(script_name: str) -> BinaryIO:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return (LOG_DIR / f"{script_name}.log").open("wb")


def _close_log(script_name: str) -> None:
    handle = _log_handles.pop(script_name, None)
    if handle is not None and not handle.closed:
        handle.close()


def _platform_name() -> str:
    return os.name


def _kill_process_group(process_group_id: int, selected_signal: int) -> None:
    """Send a POSIX signal without requiring ``os.killpg`` on Windows imports."""
    killpg = getattr(os, "killpg", None)
    if killpg is None:
        raise RuntimeError("POSIX process-group signalling is unavailable.")
    killpg(process_group_id, selected_signal)


async def _spawn_process(
    command: list[str], log_handle: BinaryIO
) -> asyncio.subprocess.Process:
    if _platform_name() == "nt":
        return await asyncio.create_subprocess_exec(
            *command,
            stdout=log_handle,
            stderr=log_handle,
            cwd=PROJECT_ROOT.resolve(),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    return await asyncio.create_subprocess_exec(
        *command,
        stdout=log_handle,
        stderr=log_handle,
        cwd=PROJECT_ROOT.resolve(),
        start_new_session=True,
    )


async def _reap_terminator(terminator: asyncio.subprocess.Process) -> None:
    """Bound helper shutdown and reap it before returning to the registry lock."""
    try:
        await asyncio.wait_for(
            terminator.wait(), timeout=STOP_TIMEOUT_SECONDS
        )
        return
    except asyncio.TimeoutError:
        try:
            terminator.terminate()
        except ProcessLookupError:
            pass

    try:
        await asyncio.wait_for(
            terminator.wait(), timeout=STOP_TIMEOUT_SECONDS
        )
        return
    except asyncio.TimeoutError:
        try:
            terminator.kill()
        except ProcessLookupError:
            pass

    await asyncio.wait_for(terminator.wait(), timeout=STOP_TIMEOUT_SECONDS)


def _forget_watch_task(script_name: str, task: asyncio.Task[None]) -> None:
    if _watch_tasks.get(script_name) is task:
        _watch_tasks.pop(script_name, None)


async def _watch_process(
    script_name: str, process: asyncio.subprocess.Process
) -> None:
    """Reap a spontaneous exit and release the parent-owned log handle."""
    await process.wait()
    async with _registry_lock:
        if active_processes.get(script_name) is process:
            _close_log(script_name)


async def _signal_process_tree(
    process: asyncio.subprocess.Process, *, force: bool
) -> None:
    if process.returncode is not None:
        return
    if _platform_name() == "nt":
        command = ["taskkill.exe", "/PID", str(process.pid), "/T"]
        if force:
            command.append("/F")
        terminator = await asyncio.create_subprocess_exec(
            *command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=PROJECT_ROOT.resolve(),
        )
        await _reap_terminator(terminator)
        return

    selected_signal = POSIX_KILL_SIGNAL if force else POSIX_TERMINATE_SIGNAL
    try:
        _kill_process_group(process.pid, selected_signal)
    except ProcessLookupError:
        return


async def start_script(script_name: str) -> dict[str, object]:
    command = _validate_script_name(script_name)
    async with _registry_lock:
        existing = active_processes.get(script_name)
        if existing is not None and existing.returncode is None:
            return {"status": "already_running", "pid": existing.pid}
        if existing is not None:
            _close_log(script_name)

        log_handle = _open_log(script_name)
        try:
            process = await _spawn_process(command, log_handle)
        except BaseException:
            log_handle.close()
            raise

        active_processes[script_name] = process
        _log_handles[script_name] = log_handle
        watcher = asyncio.create_task(
            _watch_process(script_name, process),
            name=f"karst-reap-{script_name}",
        )
        _watch_tasks[script_name] = watcher
        watcher.add_done_callback(partial(_forget_watch_task, script_name))
        return {"status": "started", "pid": process.pid}


async def stop_script(script_name: str) -> dict[str, object]:
    _validate_script_name(script_name)
    async with _registry_lock:
        process = active_processes.get(script_name)
        if process is None:
            return {"status": "not_running"}
        if process.returncode is not None:
            _close_log(script_name)
            return {"status": "not_running"}

        try:
            await _signal_process_tree(process, force=False)
            await asyncio.wait_for(process.wait(), timeout=STOP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            await _signal_process_tree(process, force=True)
            await asyncio.wait_for(process.wait(), timeout=STOP_TIMEOUT_SECONDS)
        finally:
            _close_log(script_name)
        return {"status": "stopped"}


def get_script_status(script_name: str) -> dict[str, object]:
    if script_name not in ALLOWED_SCRIPTS:
        return {"status": "invalid_script"}

    process = active_processes.get(script_name)
    if process is None:
        return {"status": "not_running"}
    if process.returncode is not None:
        _close_log(script_name)
        return {"status": "stopped", "exit_code": process.returncode}
    return {"status": "running", "pid": process.pid}


async def shutdown_all_scripts() -> None:
    running = [
        name for name, process in active_processes.items() if process.returncode is None
    ]
    for script_name in running:
        await stop_script(script_name)
    watchers = tuple(_watch_tasks.values())
    if watchers:
        await asyncio.gather(*watchers, return_exceptions=True)
    for script_name in tuple(_log_handles):
        _close_log(script_name)
