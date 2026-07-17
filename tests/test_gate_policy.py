import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import gate, gate_cli, gate_ruff
from tests.test_gate import FakeRunner, execute, seed_safe_project


EXPECTED_DEFAULT_MODULE_FLOORS = {
    "scripts/gate_artifacts.py": 70.0,
    "scripts/gate_cli.py": 70.0,
    "scripts/gate_ruff.py": 70.0,
    "scripts/gate_support.py": 70.0,
    "src/karst_core/database/database.py": 70.0,
    "src/karst_core/database/database_session.py": 70.0,
    "src/karst_core/database/db_generation_identity.py": 70.0,
    "src/karst_core/database/db_graph_repository.py": 70.0,
    "src/karst_core/database/db_integrity_repository.py": 70.0,
    "src/karst_core/database/db_migration_v3.py": 70.0,
    "src/karst_core/database/db_schema_v3.py": 70.0,
    "src/karst_core/database/db_schema_v3_contract.py": 70.0,
    "src/karst_core/database/db_schema_v3_expectations.py": 70.0,
    "src/git_logic.py": 70.0,
    "src/karst_core/indexing/service.py": 70.0,
    "src/karst_core/parser/facade.py": 80.0,
    "src/karst_core/parser/models.py": 80.0,
    "src/karst_core/parser/runtime.py": 80.0,
    "src/main.py": 70.0,
    "src/mission_control_transition/process_manager.py": 70.0,
    "src/mission_control_transition/runtime_store.py": 70.0,
    "src/query_logic.py": 70.0,
    "src/security.py": 85.0,
    "src/settings.py": 80.0,
    "src/tool_service.py": 70.0,
    "src/web.py": 70.0,
    "src/web_auth.py": 70.0,
    "src/web_data.py": 70.0,
    "src/web_graph.py": 70.0,
    "src/web_history.py": 70.0,
    "src/web_sessions.py": 70.0,
}


def test_default_coverage_policy_includes_git_logic_and_all_shipped_python_scope(
    tmp_path: Path,
) -> None:
    assert gate.DEFAULT_MODULE_COVERAGE_MIN == EXPECTED_DEFAULT_MODULE_FLOORS
    seed_safe_project(tmp_path)
    runner = FakeRunner()

    exit_code, report = execute(tmp_path, runner)

    assert exit_code == 0
    pytest_command = next(
        command for command in runner.commands if command[2] == "pytest"
    )
    assert "--cov=src" in pytest_command
    assert "--cov=scripts" in pytest_command
    assert report["coverage"]["scope"] == ["src", "scripts"]


def test_pytest_command_collects_only_the_tests_directory(tmp_path: Path) -> None:
    seed_safe_project(tmp_path)
    runner = FakeRunner()

    exit_code, _report = execute(tmp_path, runner)

    assert exit_code == 0
    pytest_command = next(
        command for command in runner.commands if command[2] == "pytest"
    )
    assert pytest_command[:5] == ["uv", "run", "pytest", "-q", "tests"]


def test_pytest_command_uses_a_unique_short_gate_basetemp(tmp_path: Path) -> None:
    seed_safe_project(tmp_path)
    runner = FakeRunner()

    exit_code, report = execute(tmp_path, runner)

    assert exit_code == 0
    pytest_command = next(
        command for command in runner.commands if command[2] == "pytest"
    )
    basetemp = Path(
        next(argument.split("=", 1)[1] for argument in pytest_command if argument.startswith("--basetemp="))
    )
    assert basetemp.parent == tmp_path.parent / "kgt"
    assert basetemp.name
    assert basetemp != tmp_path / report["artifacts"]["run_directory"]


def test_gate_cli_applies_overrides_and_renders_pass_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    def execute_gate(root: Path, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        observed.update(root=root, **kwargs)
        return 0, {
            "tests": {"passed": 3, "total": 3},
            "coverage": {"pct": 88.5},
            "artifacts": {"run_directory": "logs/gate-runs/pass"},
        }

    exit_code = gate_cli.run_cli(
        execute_gate,
        45.0,
        300.0,
        {"src/original.py": 70.0},
        [
            "--coverage-min",
            "55",
            "--timeout-seconds",
            "12",
            "--module-coverage",
            "src/original.py=80",
            "--module-coverage",
            "src/new.py=75",
        ],
    )

    assert exit_code == 0
    assert observed["coverage_min"] == 55.0
    assert observed["timeout_seconds"] == 12.0
    assert observed["module_coverage_min"] == {
        "src/original.py": 80.0,
        "src/new.py": 75.0,
    }
    assert "GATE PASS  tests=3/3  coverage=88.5%" in capsys.readouterr().out


def test_gate_cli_can_disable_defaults_and_bounds_failure_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    def execute_gate(root: Path, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        observed.update(root=root, **kwargs)
        return 1, {
            "failures": [f"failure-{index}" for index in range(32)],
            "artifacts": {"run_directory": "logs/gate-runs/fail"},
        }

    exit_code = gate_cli.run_cli(
        execute_gate,
        45.0,
        300.0,
        {"src/original.py": 70.0},
        ["--no-default-module-coverage"],
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert observed["module_coverage_min"] == {}
    assert "GATE FAIL" in output
    assert "(2 more; see artifacts)" in output


@pytest.mark.parametrize(
    ("parser", "value"),
    [
        (gate_cli._percentage, "not-a-number"),
        (gate_cli._percentage, "nan"),
        (gate_cli._percentage, "101"),
        (gate_cli._positive_seconds, "not-a-number"),
        (gate_cli._positive_seconds, "0"),
        (gate_cli._positive_seconds, "inf"),
        (gate_cli._module_floor, "missing-separator"),
        (gate_cli._module_floor, "=70"),
    ],
)
def test_gate_cli_rejects_invalid_policy_values(parser: Any, value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="coverage|timeout|module"):
        parser(value)


def test_ruff_validator_accepts_a_complete_finding() -> None:
    payload = json.dumps(
        [
            {
                "code": "S001",
                "filename": "unsafe.py",
                "message": "unsafe call",
                "location": {"row": 1, "column": 2},
                "end_location": {"row": 1, "column": 6},
                "url": "https://example.invalid/rule",
                "cell": 0,
                "noqa_row": 0,
                "fix": {
                    "applicability": "safe",
                    "message": "replace it",
                    "edits": [
                        {
                            "content": "safe_call()",
                            "location": {"row": 1, "column": 1},
                            "end_location": {"row": 1, "column": 7},
                        }
                    ],
                },
            }
        ]
    )
    failures: list[str] = []

    findings = gate_ruff.validate_ruff_output(payload, failures)

    assert failures == []
    assert findings == ["[G1 ruff] unsafe.py:1 unsafe call (S001)"]


@pytest.mark.parametrize(
    "ruff_stdout",
    [
        "",
        "null",
        "{}",
        "[null]",
        '[{"code":"S001","filename":"bad.py","message":"bad","location":[]}]',
        '[{"code":"S001","filename":"bad.py","message":"bad","location":{"row":"1","column":1}}]',
        '[{"code":"S001","filename":"bad.py","message":"bad","location":{"row":1,"column":1},"fix":[]}]',
        '[{"code":"S001","filename":"bad.py","message":"bad","location":{"row":1,"column":1},"end_location":[]}]',
    ],
)
def test_malformed_ruff_output_fails_and_persists_report(
    tmp_path: Path, ruff_stdout: str
) -> None:
    seed_safe_project(tmp_path)

    exit_code, report = execute(tmp_path, FakeRunner(ruff_stdout=ruff_stdout))

    assert exit_code == 1
    assert any("[G1 ruff]" in failure for failure in report["failures"])
    run_directory = tmp_path / report["artifacts"]["run_directory"]
    persisted = json.loads(
        (run_directory / "gate-report.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "failed"
