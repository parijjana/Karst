from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Iterator
from io import BytesIO
from typing import Any, cast

import pytest

from src.mission_control_transition import process_manager


class FakeProcess:
    def __init__(self, pid: int = 42, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9

    async def wait(self) -> int:
        self.wait_calls += 1
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode


@pytest.fixture(autouse=True)
def reset_registry() -> Iterator[None]:
    process_manager.active_processes.clear()
    process_manager._log_handles.clear()
    process_manager._watch_tasks.clear()
    yield
    for handle in process_manager._log_handles.values():
        handle.close()
    process_manager.active_processes.clear()
    process_manager._log_handles.clear()
    process_manager._watch_tasks.clear()


def test_concurrent_starts_admit_exactly_one_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = 0
    handle = BytesIO()

    class RunningProcess(FakeProcess):
        async def wait(self) -> int:
            self.wait_calls += 1
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    async def create(*_args: Any, **_kwargs: Any) -> FakeProcess:
        nonlocal created
        created += 1
        await asyncio.sleep(0)
        return RunningProcess()

    monkeypatch.setattr(process_manager.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(process_manager, "_open_log", lambda _name: handle)

    async def run_concurrently() -> tuple[dict[str, object], dict[str, object]]:
        return await asyncio.gather(
            process_manager.start_script("watchdog"),
            process_manager.start_script("watchdog"),
        )

    first, second = asyncio.run(run_concurrently())

    assert created == 1
    assert {first["status"], second["status"]} == {
        "started",
        "already_running",
    }


def test_spawn_uses_canonical_working_directory_and_isolated_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    handle = BytesIO()

    async def create(*args: Any, **kwargs: Any) -> FakeProcess:
        observed["args"] = args
        observed.update(kwargs)
        return FakeProcess(returncode=0)

    monkeypatch.setattr(process_manager.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(process_manager, "_open_log", lambda _name: handle)
    monkeypatch.setattr(process_manager, "_platform_name", lambda: "nt")

    asyncio.run(process_manager.start_script("watchdog"))

    assert observed["cwd"] == process_manager.PROJECT_ROOT.resolve()
    assert observed["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP
    assert "preexec_fn" not in observed


def test_posix_spawn_uses_native_new_session_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    handle = BytesIO()

    async def create(*_args: Any, **kwargs: Any) -> FakeProcess:
        observed.update(kwargs)
        return FakeProcess(returncode=0)

    monkeypatch.setattr(process_manager.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(process_manager, "_open_log", lambda _name: handle)
    monkeypatch.setattr(process_manager, "_platform_name", lambda: "posix")

    asyncio.run(process_manager.start_script("watchdog"))

    assert observed["start_new_session"] is True
    assert "creationflags" not in observed


def test_spawn_failure_closes_log_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    handle = BytesIO()

    async def fail(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("spawn failed")

    monkeypatch.setattr(process_manager.asyncio, "create_subprocess_exec", fail)
    monkeypatch.setattr(process_manager, "_open_log", lambda _name: handle)

    with pytest.raises(OSError, match="spawn failed"):
        asyncio.run(process_manager.start_script("watchdog"))

    assert handle.closed
    assert process_manager.active_processes == {}


def test_stop_terminates_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess()
    handle = BytesIO()
    process_manager.active_processes["watchdog"] = process  # type: ignore[assignment]
    process_manager._log_handles["watchdog"] = handle

    async def signal_tree(target: FakeProcess, force: bool) -> None:
        assert not force
        target.terminate()

    monkeypatch.setattr(process_manager, "_signal_process_tree", signal_tree)

    result = asyncio.run(process_manager.stop_script("watchdog"))

    assert result == {"status": "stopped"}
    assert process.terminate_calls == 1
    assert process.wait_calls == 1
    assert handle.closed
    assert process_manager.get_script_status("watchdog") == {
        "status": "stopped",
        "exit_code": 0,
    }


def test_stop_kills_and_reaps_after_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess()
    process_manager.active_processes["watchdog"] = process  # type: ignore[assignment]

    calls = 0

    async def fake_wait_for(awaitable, timeout: float):
        nonlocal calls
        calls += 1
        if calls == 1:
            awaitable.close()
            raise asyncio.TimeoutError
        return await awaitable

    monkeypatch.setattr(process_manager.asyncio, "wait_for", fake_wait_for)

    async def signal_tree(target: FakeProcess, force: bool) -> None:
        if force:
            target.kill()
        else:
            target.terminate()

    monkeypatch.setattr(process_manager, "_signal_process_tree", signal_tree)

    result = asyncio.run(process_manager.stop_script("watchdog"))

    assert result == {"status": "stopped"}
    assert process.kill_calls == 1
    assert process.wait_calls == 1


def test_spontaneous_exit_is_reaped_and_closes_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = BytesIO()

    class ControlledProcess(FakeProcess):
        def __init__(self) -> None:
            super().__init__(pid=91)
            self.finished = asyncio.Event()

        async def wait(self) -> int:
            self.wait_calls += 1
            await self.finished.wait()
            assert self.returncode is not None
            return self.returncode

    async def exercise() -> dict[str, object]:
        process = ControlledProcess()

        async def create(*_args: Any, **_kwargs: Any) -> ControlledProcess:
            return process

        monkeypatch.setattr(process_manager.asyncio, "create_subprocess_exec", create)
        monkeypatch.setattr(process_manager, "_open_log", lambda _name: handle)
        await process_manager.start_script("watchdog")
        process.returncode = 7
        process.finished.set()
        await process_manager._watch_tasks["watchdog"]
        return process_manager.get_script_status("watchdog")

    status = asyncio.run(exercise())

    assert status == {"status": "stopped", "exit_code": 7}
    assert handle.closed


def test_shutdown_stops_every_running_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = FakeProcess(pid=1)
    second = FakeProcess(pid=2)
    process_manager.active_processes.update(
        {
            "watchdog": cast(asyncio.subprocess.Process, first),
            "reindexer": cast(asyncio.subprocess.Process, second),
        }
    )

    async def signal_tree(target: FakeProcess, force: bool) -> None:
        assert not force
        target.terminate()

    monkeypatch.setattr(process_manager, "_signal_process_tree", signal_tree)

    asyncio.run(process_manager.shutdown_all_scripts())

    assert first.terminate_calls == second.terminate_calls == 1
    assert first.returncode == second.returncode == 0


def test_invalid_and_inactive_status_paths() -> None:
    with pytest.raises(ValueError):
        asyncio.run(process_manager.start_script("unknown"))
    with pytest.raises(ValueError):
        asyncio.run(process_manager.stop_script("unknown"))
    assert asyncio.run(process_manager.stop_script("watchdog")) == {
        "status": "not_running"
    }
    assert process_manager.get_script_status("unknown") == {"status": "invalid_script"}
    assert process_manager.get_script_status("watchdog") == {"status": "not_running"}
