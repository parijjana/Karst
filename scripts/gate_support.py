import ast
import os
import shlex
import signal
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_SHIPPED_FILE_LINES = 300
SECURITY_GUARDRAIL = (
    "Narrow AST guardrail for wildcard imports, os.system/os.popen, and "
    "shell=True on known subprocess APIs; it is not a full security analyzer."
)


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    error: str | None
    timed_out: bool


Runner = Callable[[list[str], float, Path], CommandResult]


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            kill_process_group = getattr(os, "killpg")
            kill_process_group(process.pid, getattr(signal, "SIGKILL"))
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


def run_command(command: list[str], timeout_seconds: float, cwd: Path) -> CommandResult:
    popen_options: dict[str, Any] = {}
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **popen_options,
        )
    except FileNotFoundError as exc:
        return CommandResult(command, None, "", "", f"tool not found: {exc}", False)
    except OSError as exc:
        return CommandResult(
            command, None, "", "", f"could not start tool: {exc}", False
        )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return CommandResult(command, process.returncode, stdout, stderr, None, False)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        return CommandResult(
            command,
            process.returncode,
            stdout,
            stderr,
            f"timed out after {timeout_seconds:g} seconds; process tree terminated",
            True,
        )


def discover_python_files(root: Path) -> list[str]:
    files: set[Path] = set(root.glob("*.py"))
    for directory_name in ("src", "scripts", "tests"):
        directory = root / directory_name
        if directory.is_dir():
            files.update(directory.rglob("*.py"))
    return sorted(path.relative_to(root).as_posix() for path in files if path.is_file())


def _import_aliases(tree: ast.AST) -> tuple[set[str], set[str], set[str], set[str]]:
    os_modules = {"os"}
    os_functions: set[str] = set()
    subprocess_modules = {"subprocess"}
    subprocess_functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    os_modules.add(alias.asname or alias.name)
                elif alias.name == "subprocess":
                    subprocess_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                local_name = alias.asname or alias.name
                if node.module == "os" and alias.name in {"system", "popen"}:
                    os_functions.add(local_name)
                elif node.module == "subprocess" and alias.name in {
                    "Popen",
                    "call",
                    "check_call",
                    "check_output",
                    "run",
                }:
                    subprocess_functions.add(local_name)
    return os_modules, os_functions, subprocess_modules, subprocess_functions


def _is_true_shell_keyword(node: ast.Call) -> bool:
    return any(
        keyword.arg == "shell"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in node.keywords
    )


def source_checks(root: Path, files: list[str]) -> dict[str, list[str]]:
    failures: dict[str, list[str]] = {"size": [], "structure": [], "security": []}
    subprocess_calls = {"Popen", "call", "check_call", "check_output", "run"}
    for relative_path in files:
        try:
            content = (root / relative_path).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            failures["structure"].append(f"[G3 read] {relative_path}: {exc}")
            continue
        line_count = len(content.splitlines())
        if line_count > MAX_SHIPPED_FILE_LINES:
            failures["size"].append(
                f"[G2 size] {relative_path} {line_count} > {MAX_SHIPPED_FILE_LINES}"
            )
        try:
            tree = ast.parse(content, filename=relative_path)
        except SyntaxError as exc:
            failures["structure"].append(
                f"[G3 syntax] {relative_path}:{exc.lineno or 0}: {exc.msg}"
            )
            continue
        os_modules, os_functions, subprocess_modules, subprocess_functions = (
            _import_aliases(tree)
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "*" for alias in node.names
            ):
                failures["structure"].append(
                    f"[G3 import] {relative_path}:{node.lineno}: wildcard import is forbidden"
                )
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            os_call = isinstance(function, ast.Name) and function.id in os_functions
            os_call = os_call or (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id in os_modules
                and function.attr in {"system", "popen"}
            )
            if os_call:
                failures["security"].append(
                    f"[G6 security] {relative_path}:{node.lineno}: os process shell API is forbidden"
                )
            subprocess_call = (
                isinstance(function, ast.Name) and function.id in subprocess_functions
            )
            subprocess_call = subprocess_call or (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id in subprocess_modules
                and function.attr in subprocess_calls
            )
            if subprocess_call and _is_true_shell_keyword(node):
                failures["security"].append(
                    f"[G6 security] {relative_path}:{node.lineno}: subprocess shell=True is forbidden"
                )
    return failures


def run_tool(
    name: str,
    command: list[str],
    root: Path,
    run_directory: Path,
    runner: Runner,
    timeout_seconds: float,
) -> tuple[CommandResult, dict[str, Any], list[str]]:
    result = runner(command, timeout_seconds, root)
    parts = [
        f"command: {shlex.join(result.command)}",
        f"cwd: {root}",
        f"returncode: {result.returncode}",
        f"timed_out: {result.timed_out}",
        f"error: {result.error or ''}",
        "--- stdout ---",
        result.stdout,
        "--- stderr ---",
        result.stderr,
    ]
    (run_directory / f"{name}.log").write_text("\n".join(parts), encoding="utf-8")
    failures: list[str] = []
    if result.error:
        failures.append(f"[G1 {name}] {result.error}")
    if result.returncode != 0:
        failures.append(f"[G1 {name}] exited with status {result.returncode}")
    status = "passed" if not failures else "failed"
    return result, {"status": status, "returncode": result.returncode}, failures
