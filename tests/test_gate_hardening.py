import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from scripts import gate
from tests.test_gate import FakeRunner, coverage_payload, execute, seed_safe_project


def valid_pytest_payload() -> dict[str, Any]:
    return {
        "created": 1.0,
        "duration": 0.1,
        "exitcode": 0,
        "summary": {"total": 1, "collected": 1, "passed": 1},
        "tests": [{"nodeid": "tests/test_safe.py::test_safe", "outcome": "passed"}],
    }


@pytest.mark.parametrize(
    ("artifact", "payload"),
    [
        ("pytest", {"summary": {"total": -1, "passed": -1}, "tests": []}),
        (
            "pytest",
            {"exitcode": 0, "summary": {"total": 1.5, "passed": 1}, "tests": []},
        ),
        (
            "pytest",
            json.dumps(
                {
                    "created": math.nan,
                    "duration": 0,
                    "exitcode": 0,
                    "summary": {},
                    "tests": [],
                }
            ),
        ),
        ("coverage", {"totals": {"percent_covered": 101}, "files": {}}),
        (
            "coverage",
            {
                "totals": {
                    "covered_lines": -1,
                    "num_statements": 1,
                    "missing_lines": 2,
                    "excluded_lines": 0,
                    "percent_covered": -100,
                },
                "files": {},
            },
        ),
        (
            "coverage",
            json.dumps({"totals": {"percent_covered": math.inf}, "files": {}}),
        ),
    ],
)
def test_malformed_artifacts_fail_and_still_emit_run_report(
    tmp_path: Path, artifact: str, payload: dict[str, Any] | str
) -> None:
    seed_safe_project(tmp_path)
    runner = FakeRunner(
        pytest_payload=payload if artifact == "pytest" else valid_pytest_payload(),
        coverage_payload=payload if artifact == "coverage" else coverage_payload(8, 10),
    )

    exit_code, report = execute(tmp_path, runner)

    assert exit_code == 1
    assert report["tests" if artifact == "pytest" else "coverage"]["status"] == "failed"
    run_directory = tmp_path / report["artifacts"]["run_directory"]
    persisted = json.loads(
        (run_directory / "gate-report.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "failed"


def test_coverage_enforces_configurable_critical_module_floor(tmp_path: Path) -> None:
    seed_safe_project(tmp_path)
    payload = coverage_payload(8, 10)
    payload["files"]["src/safe.py"]["summary"] = coverage_payload(4, 10)["totals"]

    exit_code, report = gate.execute_gate(
        tmp_path,
        runner=FakeRunner(coverage_payload=payload),
        coverage_min=45.0,
        module_coverage_min={"src/safe.py": 60.0},
    )

    assert exit_code == 1
    assert report["coverage"]["modules"]["src/safe.py"]["pct"] == 40.0
    assert report["coverage"]["modules"]["src/safe.py"]["status"] == "failed"


def test_run_command_uses_requested_working_directory(tmp_path: Path) -> None:
    result = gate.run_command(
        [sys.executable, "-c", "from pathlib import Path; print(Path.cwd())"],
        5.0,
        tmp_path,
    )

    assert result.returncode == 0
    assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()


@pytest.mark.parametrize("timeout", [0.0, -1.0, math.nan, math.inf])
def test_execute_gate_rejects_nonfinite_or_nonpositive_timeout(
    tmp_path: Path, timeout: float
) -> None:
    seed_safe_project(tmp_path)
    with pytest.raises(ValueError, match="timeout"):
        execute(tmp_path, FakeRunner(), timeout_seconds=timeout)


def test_timeout_terminates_child_process_tree(tmp_path: Path) -> None:
    sentinel = tmp_path / "child-survived"
    child_code = f"import pathlib,time; time.sleep(2); pathlib.Path({str(sentinel)!r}).write_text('alive')"
    parent_code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(60)"
    )

    result = gate.run_command([sys.executable, "-c", parent_code], 0.75, tmp_path)
    time.sleep(2.25)

    assert result.timed_out is True
    assert result.returncode is not None
    assert not sentinel.exists()


@pytest.mark.parametrize(
    "source",
    [
        "import os as operating_system\noperating_system.system('bad')\n",
        "from os import popen as launch\nlaunch('bad')\n",
        "import subprocess as sp\nsp.run(['ok'], shell=True)\n",
        "from subprocess import Popen as launch\nlaunch(['ok'], shell=True)\n",
    ],
)
def test_security_guardrail_detects_os_and_subprocess_aliases(
    tmp_path: Path, source: str
) -> None:
    seed_safe_project(tmp_path)
    (tmp_path / "unsafe.py").write_text(source, encoding="utf-8")

    exit_code, report = execute(tmp_path, FakeRunner())

    assert exit_code == 1
    assert report["security"]["status"] == "failed"
    assert "guardrail" in report["security"]


def test_security_guardrail_does_not_flag_unrelated_shell_keyword(
    tmp_path: Path,
) -> None:
    seed_safe_project(tmp_path)
    (tmp_path / "safe_helper.py").write_text("helper(shell=True)\n", encoding="utf-8")

    exit_code, report = execute(tmp_path, FakeRunner())

    assert exit_code == 0
    assert report["security"]["violations"] == []
