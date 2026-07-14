import argparse
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _percentage(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("coverage percentage must be numeric") from exc
    if not math.isfinite(parsed) or not 0 <= parsed <= 100:
        raise argparse.ArgumentTypeError(
            "coverage percentage must be finite and between 0 and 100"
        )
    return parsed


def _positive_seconds(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be numeric") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and greater than zero")
    return parsed


def _module_floor(value: str) -> tuple[str, float]:
    path, separator, floor = value.rpartition("=")
    if not separator or not path:
        raise argparse.ArgumentTypeError(
            "module floor must use relative/path.py=PERCENT"
        )
    return path.replace("\\", "/"), _percentage(floor)


def run_cli(
    execute_gate: Callable[..., tuple[int, dict[str, Any]]],
    default_coverage: float,
    default_timeout: float,
    default_modules: dict[str, float],
    argv: list[str] | None,
) -> int:
    policy_text = ", ".join(
        f"{path}={floor:g}" for path, floor in default_modules.items()
    )
    parser = argparse.ArgumentParser(
        description="Run Karst's fail-closed quality gate.",
        epilog=f"Default critical-module coverage floors: {policy_text}",
    )
    parser.add_argument(
        "--coverage-min",
        type=_percentage,
        default=default_coverage,
        help="aggregate line coverage floor across shipped src/ and scripts/ Python",
    )
    parser.add_argument(
        "--timeout-seconds", type=_positive_seconds, default=default_timeout
    )
    parser.add_argument(
        "--module-coverage",
        action="append",
        type=_module_floor,
        default=[],
        metavar="PATH=PERCENT",
        help="add or override a critical-module line coverage floor",
    )
    parser.add_argument(
        "--no-default-module-coverage",
        action="store_true",
        help="disable default critical-module floors (explicit local diagnostics only)",
    )
    args = parser.parse_args(argv)
    module_policy = {} if args.no_default_module_coverage else dict(default_modules)
    module_policy.update(dict(args.module_coverage))
    exit_code, report = execute_gate(
        Path.cwd(),
        coverage_min=args.coverage_min,
        module_coverage_min=module_policy,
        timeout_seconds=args.timeout_seconds,
    )
    if exit_code == 0:
        tests = report["tests"]
        print(
            f"GATE PASS  tests={tests['passed']}/{tests['total']}  "
            f"coverage={report['coverage']['pct']}%  "
            f"artifacts={report['artifacts']['run_directory']}"
        )
    else:
        print(f"GATE FAIL  artifacts={report['artifacts']['run_directory']}")
        for failure in report["failures"][:30]:
            print(failure)
        if len(report["failures"]) > 30:
            print(f"({len(report['failures']) - 30} more; see artifacts)")
    return exit_code
