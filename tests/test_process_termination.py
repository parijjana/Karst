from __future__ import annotations

import asyncio
from typing import cast

import pytest

from src.mission_control_transition import process_manager
from tests.test_process_manager import FakeProcess


def test_posix_termination_signals_the_entire_session_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int]] = []
    process = FakeProcess(pid=77)
    monkeypatch.setattr(process_manager, "_platform_name", lambda: "posix")
    monkeypatch.setattr(
        process_manager,
        "_kill_process_group",
        lambda pid, sig: calls.append((pid, sig)),
    )

    async def exercise() -> None:
        typed_process = cast(asyncio.subprocess.Process, process)
        await process_manager._signal_process_tree(typed_process, force=False)
        await process_manager._signal_process_tree(typed_process, force=True)

    asyncio.run(exercise())

    assert calls == [
        (77, process_manager.POSIX_TERMINATE_SIGNAL),
        (77, process_manager.POSIX_KILL_SIGNAL),
    ]


def test_windows_termination_uses_native_tree_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    async def create(*args: object, **_kwargs: object) -> FakeProcess:
        calls.append(args)
        return FakeProcess(returncode=0)

    monkeypatch.setattr(process_manager, "_platform_name", lambda: "nt")
    monkeypatch.setattr(process_manager.asyncio, "create_subprocess_exec", create)

    process = cast(asyncio.subprocess.Process, FakeProcess(pid=81))
    asyncio.run(process_manager._signal_process_tree(process, force=True))

    assert calls == [("taskkill.exe", "/PID", "81", "/T", "/F")]


def test_windows_hung_taskkill_is_bounded_killed_and_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NeverReturningTaskkill(FakeProcess):
        def __init__(self) -> None:
            super().__init__(pid=82)
            self.killed = asyncio.Event()
            self.reaped = False

        def terminate(self) -> None:
            self.terminate_calls += 1

        def kill(self) -> None:
            self.kill_calls += 1
            self.returncode = -9
            self.killed.set()

        async def wait(self) -> int:
            self.wait_calls += 1
            await self.killed.wait()
            self.reaped = True
            assert self.returncode is not None
            return self.returncode

    terminator = NeverReturningTaskkill()

    async def create(*_args: object, **_kwargs: object) -> NeverReturningTaskkill:
        return terminator

    monkeypatch.setattr(process_manager, "_platform_name", lambda: "nt")
    monkeypatch.setattr(process_manager, "STOP_TIMEOUT_SECONDS", 0.04)
    monkeypatch.setattr(process_manager.asyncio, "create_subprocess_exec", create)

    async def exercise() -> None:
        process = cast(asyncio.subprocess.Process, FakeProcess(pid=83))
        await asyncio.wait_for(
            process_manager._signal_process_tree(process, force=True), timeout=0.25
        )
        pending = [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
        assert pending == []

    asyncio.run(exercise())

    assert terminator.terminate_calls == 1
    assert terminator.kill_calls == 1
    assert terminator.reaped is True
