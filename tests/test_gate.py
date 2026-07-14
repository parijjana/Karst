import json
from pathlib import Path
from typing import Any

import pytest

from scripts import gate


class FakeRunner:
    def __init__(
        self,
        *,
        fail_tool: str | None = None,
        failure_kind: str | None = None,
        coverage_kind: str = "valid",
        test_kind: str = "passing",
        pytest_payload: dict[str, Any] | str | None = None,
        coverage_payload: dict[str, Any] | str | None = None,
        ruff_stdout: str = "[]",
    ) -> None:
        self.fail_tool = fail_tool
        self.failure_kind = failure_kind
        self.coverage_kind = coverage_kind
        self.test_kind = test_kind
        self.pytest_payload = pytest_payload
        self.coverage_payload = coverage_payload
        self.ruff_stdout = ruff_stdout
        self.commands: list[list[str]] = []
        self.working_directories: list[Path] = []

    def __call__(
        self, command: list[str], timeout_seconds: float, cwd: Path
    ) -> gate.CommandResult:
        del timeout_seconds
        self.commands.append(command)
        self.working_directories.append(cwd)
        tool = command[2] if command[:2] == ["uv", "run"] else command[0]

        if tool == self.fail_tool:
            if self.failure_kind == "missing":
                return gate.CommandResult(
                    command, None, "", "", "tool not found", False
                )
            if self.failure_kind == "timeout":
                return gate.CommandResult(command, None, "", "", "timed out", True)
            return gate.CommandResult(
                command, 2, "simulated stdout", "simulated stderr", None, False
            )

        if tool == "ruff":
            return gate.CommandResult(
                command, 0, self.ruff_stdout, "ruff details", None, False
            )
        if tool == "mypy":
            return gate.CommandResult(
                command, 0, "Success: no issues found", "", None, False
            )
        if tool == "pytest":
            report_path = Path(
                next(
                    arg.split("=", 1)[1]
                    for arg in command
                    if arg.startswith("--json-report-file=")
                )
            )
            coverage_path = Path(
                next(
                    arg.split(":", 1)[1]
                    for arg in command
                    if arg.startswith("--cov-report=json:")
                )
            )
            if self.test_kind != "collection-failure":
                failed = 1 if self.test_kind == "unit-failure" else 0
                tests = [
                    {
                        "nodeid": f"tests/test_seed.py::test_seed_{index}",
                        "outcome": "failed" if failed and index == 0 else "passed",
                    }
                    for index in range(3)
                ]
                payload: dict[str, Any] | str = self.pytest_payload or {
                    "created": 1.0,
                    "duration": 0.1,
                    "exitcode": 1 if failed else 0,
                    "summary": {
                        "total": 3,
                        "collected": 3,
                        "passed": 3 - failed,
                        "failed": failed,
                    },
                    "tests": tests,
                }
                report_path.write_text(
                    payload if isinstance(payload, str) else json.dumps(payload),
                    encoding="utf-8",
                )
            if self.coverage_kind == "valid":
                payload = self.coverage_payload or coverage_payload(8, 10)
                coverage_path.write_text(
                    payload if isinstance(payload, str) else json.dumps(payload),
                    encoding="utf-8",
                )
            elif self.coverage_kind == "low":
                coverage_path.write_text(
                    json.dumps(coverage_payload(1, 10)), encoding="utf-8"
                )
            elif self.coverage_kind == "malformed":
                coverage_path.write_text("not json", encoding="utf-8")

            return_code = 0
            if self.test_kind == "unit-failure":
                return_code = 1
            elif self.test_kind == "collection-failure":
                return_code = 2
            return gate.CommandResult(
                command, return_code, "pytest details", "", None, False
            )
        return gate.CommandResult(command, 0, "deadbeef\n", "", None, False)


def coverage_payload(covered: int, statements: int) -> dict[str, Any]:
    missing = statements - covered
    percentage = 100.0 if statements == 0 else covered / statements * 100.0
    summary = {
        "covered_lines": covered,
        "num_statements": statements,
        "missing_lines": missing,
        "excluded_lines": 0,
        "percent_covered": percentage,
    }
    return {
        "totals": summary,
        "files": {"src/safe.py": {"summary": dict(summary)}},
    }


def execute(
    root: Path, runner: FakeRunner, **kwargs: Any
) -> tuple[int, dict[str, Any]]:
    return gate.execute_gate(root, runner=runner, module_coverage_min={}, **kwargs)


def seed_safe_project(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "safe.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_safe.py").write_text(
        "def test_safe():\n    assert True\n", encoding="utf-8"
    )


@pytest.mark.parametrize("tool", ["ruff", "mypy", "pytest"])
@pytest.mark.parametrize("failure_kind", ["nonzero", "missing", "timeout"])
def test_gate_fails_for_every_tool_process_failure(
    tmp_path: Path, tool: str, failure_kind: str
) -> None:
    seed_safe_project(tmp_path)
    runner = FakeRunner(fail_tool=tool, failure_kind=failure_kind)

    exit_code, report = execute(tmp_path, runner)

    assert exit_code == 1
    assert report["tools"][tool]["status"] == "failed"


@pytest.mark.parametrize("test_kind", ["collection-failure", "unit-failure"])
def test_gate_fails_for_pytest_collection_and_test_failures(
    tmp_path: Path, test_kind: str
) -> None:
    seed_safe_project(tmp_path)

    exit_code, report = execute(tmp_path, FakeRunner(test_kind=test_kind))

    assert exit_code == 1
    assert report["tests"]["status"] == "failed"


@pytest.mark.parametrize("coverage_kind", ["missing", "malformed", "low"])
def test_gate_fails_for_invalid_or_below_floor_coverage(
    tmp_path: Path, coverage_kind: str
) -> None:
    seed_safe_project(tmp_path)

    exit_code, report = execute(
        tmp_path, FakeRunner(coverage_kind=coverage_kind), coverage_min=45.0
    )

    assert exit_code == 1
    assert report["coverage"]["status"] == "failed"
    assert report["coverage"]["minimum_pct"] == 45.0


def test_gate_never_consumes_legacy_stale_reports(tmp_path: Path) -> None:
    seed_safe_project(tmp_path)
    (tmp_path / ".report.json").write_text(
        json.dumps({"summary": {"total": 99, "passed": 99, "failed": 0}}),
        encoding="utf-8",
    )
    (tmp_path / "coverage.json").write_text(
        json.dumps({"totals": {"percent_covered": 100.0}}), encoding="utf-8"
    )

    exit_code, report = execute(
        tmp_path,
        FakeRunner(coverage_kind="missing", test_kind="collection-failure"),
    )

    assert exit_code == 1
    assert report["tests"]["total"] == 0
    assert report["coverage"]["pct"] is None


@pytest.mark.parametrize("relative_path", ["scripts/unsafe.py", "unsafe_utility.py"])
def test_gate_security_scan_includes_scripts_and_top_level_utilities(
    tmp_path: Path, relative_path: str
) -> None:
    seed_safe_project(tmp_path)
    unsafe_path = tmp_path / relative_path
    unsafe_path.parent.mkdir(exist_ok=True)
    unsafe_path.write_text("import os\nos.system('unsafe')\n", encoding="utf-8")

    exit_code, report = execute(tmp_path, FakeRunner())

    assert exit_code == 1
    assert report["security"]["status"] == "failed"
    assert any(relative_path in item for item in report["security"]["violations"])


def test_gate_passes_all_python_targets_to_static_tools_and_preserves_logs(
    tmp_path: Path,
) -> None:
    seed_safe_project(tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "worker.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "utility.py").write_text("VALUE = 1\n", encoding="utf-8")
    runner = FakeRunner()

    exit_code, report = execute(tmp_path, runner)

    assert exit_code == 0
    ruff_command = next(command for command in runner.commands if command[2] == "ruff")
    for expected in [
        "src/safe.py",
        "tests/test_safe.py",
        "scripts/worker.py",
        "utility.py",
    ]:
        assert expected in ruff_command
    run_directory = tmp_path / report["artifacts"]["run_directory"]
    ruff_log = (run_directory / "ruff.log").read_text(encoding="utf-8")
    assert "ruff details" in ruff_log
    assert (run_directory / "pytest-report.json").is_file()
    assert (run_directory / "coverage.json").is_file()
    assert runner.working_directories
    assert set(runner.working_directories) == {tmp_path.resolve()}
