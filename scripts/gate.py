import json
import math
import sys
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__:
    from .gate_artifacts import (
        load_json,
        validate_coverage_report,
        validate_pytest_report,
    )
    from .gate_cli import run_cli
    from .gate_ruff import validate_ruff_output
    from .gate_support import (
        SECURITY_GUARDRAIL,
        CommandResult,
        Runner,
        discover_python_files,
        run_command,
        run_tool,
        source_checks,
    )
else:
    from gate_artifacts import (  # type: ignore[import-not-found,no-redef]
        load_json,
        validate_coverage_report,
        validate_pytest_report,
    )
    from gate_cli import run_cli  # type: ignore[import-not-found,no-redef]
    from gate_ruff import validate_ruff_output  # type: ignore[import-not-found,no-redef]
    from gate_support import (  # type: ignore[import-not-found,no-redef]
        SECURITY_GUARDRAIL,
        CommandResult,
        Runner,
        discover_python_files,
        run_command,
        run_tool,
        source_checks,
    )

DEFAULT_COVERAGE_MIN = 45.0
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MODULE_COVERAGE_MIN = {
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
__all__ = ["CommandResult", "execute_gate", "main", "run_command"]


def _validated_percentage(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or not 0 <= parsed <= 100:
        raise ValueError(f"{label} must be finite and between 0 and 100")
    return parsed


def _validated_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError("timeout must be finite and greater than zero")
    return parsed


def _module_policy(policy: Mapping[str, float] | None) -> dict[str, float]:
    selected = DEFAULT_MODULE_COVERAGE_MIN if policy is None else policy
    validated: dict[str, float] = {}
    for path, floor in selected.items():
        normalized = path.replace("\\", "/")
        if not normalized or Path(normalized).is_absolute():
            raise ValueError("module coverage paths must be non-empty and relative")
        validated[normalized] = _validated_percentage(
            floor, f"module floor {normalized}"
        )
    return validated


def execute_gate(
    root: Path,
    *,
    runner: Runner = run_command,
    coverage_min: float = DEFAULT_COVERAGE_MIN,
    module_coverage_min: Mapping[str, float] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, dict[str, Any]]:
    root = root.resolve()
    coverage_min = _validated_percentage(coverage_min, "aggregate coverage floor")
    timeout_seconds = _validated_timeout(timeout_seconds)
    module_policy = _module_policy(module_coverage_min)
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + f"-{uuid.uuid4().hex[:8]}"
    )
    run_directory = root / "logs" / "gate-runs" / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    pytest_report_path = run_directory / "pytest-report.json"
    coverage_path = run_directory / "coverage.json"
    # Keep pytest worktrees short enough for Windows Git's nested lock paths.
    pytest_basetemp = root.parent / "kgt" / run_id
    pytest_basetemp.parent.mkdir(parents=True, exist_ok=True)
    files = discover_python_files(root)
    source_failures = source_checks(root, files)
    all_failures = [
        *source_failures["size"],
        *source_failures["structure"],
        *source_failures["security"],
    ]

    tools: dict[str, Any] = {}
    ruff_result, tools["ruff"], failures = run_tool(
        "ruff",
        ["uv", "run", "ruff", "check", *files, "--output-format=json"],
        root,
        run_directory,
        runner,
        timeout_seconds,
    )
    all_failures.extend(failures)
    ruff_schema_failures: list[str] = []
    ruff_findings = validate_ruff_output(ruff_result.stdout, ruff_schema_failures)
    if ruff_schema_failures or ruff_findings:
        tools["ruff"]["status"] = "failed"
    all_failures.extend(ruff_schema_failures)
    all_failures.extend(ruff_findings)

    _, tools["mypy"], failures = run_tool(
        "mypy",
        ["uv", "run", "mypy", *files],
        root,
        run_directory,
        runner,
        timeout_seconds,
    )
    all_failures.extend(failures)
    pytest_command = [
        "uv",
        "run",
        "pytest",
        "-q",
        "tests",
        f"--basetemp={pytest_basetemp}",
        "--json-report",
        f"--json-report-file={pytest_report_path}",
        "--cov=src",
        "--cov=scripts",
        "--cov-report=",
        f"--cov-report=json:{coverage_path}",
        f"--cov-fail-under={coverage_min:g}",
    ]
    pytest_result, tools["pytest"], failures = run_tool(
        "pytest", pytest_command, root, run_directory, runner, timeout_seconds
    )
    all_failures.extend(failures)

    test_failures: list[str] = []
    test_data = load_json(pytest_report_path, "[G4 test] pytest report", test_failures)
    test_stats = validate_pytest_report(test_data, test_failures)
    if (
        test_stats["exitcode"] >= 0
        and pytest_result.returncode is not None
        and test_stats["exitcode"] != pytest_result.returncode
    ):
        test_failures.append(
            "[G4 test] artifact exitcode does not match pytest process"
        )
    if test_stats["failed"]:
        test_failures.append(
            f"[G4 test] pytest reported {test_stats['failed']} failed/error tests"
        )
    if tools["pytest"]["status"] == "failed":
        test_failures.append("[G4 test] pytest process did not complete successfully")
    all_failures.extend(test_failures)

    coverage_failures: list[str] = []
    coverage_data = load_json(
        coverage_path, "[G5 coverage] coverage report", coverage_failures
    )
    coverage_pct, observed_modules = validate_coverage_report(
        coverage_data, coverage_failures
    )
    if coverage_pct is not None and coverage_pct < coverage_min:
        coverage_failures.append(
            f"[G5 coverage] {coverage_pct:.2f}% is below the {coverage_min:.2f}% aggregate floor"
        )
    module_results: dict[str, dict[str, Any]] = {}
    for path, minimum in module_policy.items():
        observed = observed_modules.get(path)
        module_failed = observed is None or observed < minimum
        if observed is None:
            coverage_failures.append(f"[G5 coverage] required module is absent: {path}")
        elif module_failed:
            coverage_failures.append(
                f"[G5 coverage] {path} {observed:.2f}% is below the {minimum:.2f}% floor"
            )
        module_results[path] = {
            "status": "failed" if module_failed else "passed",
            "pct": round(observed, 2) if observed is not None else None,
            "minimum_pct": minimum,
        }
    all_failures.extend(coverage_failures)

    report: dict[str, Any] = {
        "schema_version": "2.1",
        "status": "failed" if all_failures else "passed",
        "scope": {"python_files": files},
        "tools": tools,
        "size": {
            "status": "failed" if source_failures["size"] else "passed",
            "violations": source_failures["size"],
        },
        "structure": {
            "status": "failed" if source_failures["structure"] else "passed",
            "violations": source_failures["structure"],
        },
        "security": {
            "status": "failed" if source_failures["security"] else "passed",
            "guardrail": SECURITY_GUARDRAIL,
            "violations": source_failures["security"],
        },
        "tests": {
            "status": "failed" if test_failures else "passed",
            "total": test_stats["total"],
            "passed": test_stats["passed"],
            "failed": test_stats["failed"],
            "failures": test_failures,
        },
        "coverage": {
            "status": "failed" if coverage_failures else "passed",
            "scope": ["src", "scripts"],
            "pct": round(coverage_pct, 2) if coverage_pct is not None else None,
            "minimum_pct": coverage_min,
            "modules": module_results,
            "failures": coverage_failures,
        },
        "failures": all_failures,
        "artifacts": {"run_directory": run_directory.relative_to(root).as_posix()},
    }
    rendered_report = json.dumps(report, indent=2, allow_nan=False)
    (run_directory / "gate-report.json").write_text(rendered_report, encoding="utf-8")
    (root / "gate_report.json").write_text(rendered_report, encoding="utf-8")
    return (1 if all_failures else 0), report


def main(argv: list[str] | None = None) -> int:
    return run_cli(
        execute_gate,
        DEFAULT_COVERAGE_MIN,
        DEFAULT_TIMEOUT_SECONDS,
        DEFAULT_MODULE_COVERAGE_MIN,
        argv,
    )


if __name__ == "__main__":
    sys.exit(main())
