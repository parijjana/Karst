import json
import math
from pathlib import Path
from typing import Any


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value} is forbidden")


def load_json(path: Path, label: str, failures: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        failures.append(f"{label} was not generated for this gate run")
        return None
    try:
        data = json.loads(
            path.read_text(encoding="utf-8"), parse_constant=_reject_nonfinite_json
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        failures.append(f"{label} is unreadable: {exc}")
        return None
    if not isinstance(data, dict):
        failures.append(f"{label} must contain a JSON object")
        return None
    return data


def _finite_number(
    value: Any,
    field: str,
    failures: list[str],
    *,
    minimum: float = 0,
    maximum: float | None = None,
) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        failures.append(f"{field} must be a finite number")
        return None
    number = float(value)
    if number < minimum or (maximum is not None and number > maximum):
        failures.append(f"{field} is outside the allowed range")
        return None
    return number


def _count(value: Any, field: str, failures: list[str]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        failures.append(f"{field} must be a non-negative integer")
        return None
    return value


def validate_pytest_report(
    data: dict[str, Any] | None, failures: list[str]
) -> dict[str, int]:
    stats = {"total": 0, "passed": 0, "failed": 0, "exitcode": -1}
    if data is None:
        return stats
    _finite_number(data.get("created"), "[G4 test] created", failures)
    _finite_number(data.get("duration"), "[G4 test] duration", failures)
    exitcode = _count(data.get("exitcode"), "[G4 test] exitcode", failures)
    summary = data.get("summary")
    tests = data.get("tests")
    if not isinstance(summary, dict):
        failures.append("[G4 test] summary must be an object")
        summary = {}
    if not isinstance(tests, list):
        failures.append("[G4 test] tests must be an array")
        tests = []
    outcome_keys = ("passed", "failed", "skipped", "xfailed", "xpassed", "error")
    counts = {
        key: _count(summary.get(key, 0), f"[G4 test] summary.{key}", failures)
        for key in outcome_keys
    }
    total = _count(summary.get("total"), "[G4 test] summary.total", failures)
    collected = _count(
        summary.get("collected"), "[G4 test] summary.collected", failures
    )
    observed = {key: 0 for key in outcome_keys}
    for index, item in enumerate(tests):
        if not isinstance(item, dict):
            failures.append(f"[G4 test] tests[{index}] must be an object")
            continue
        if not isinstance(item.get("nodeid"), str) or not item["nodeid"]:
            failures.append(
                f"[G4 test] tests[{index}].nodeid must be a non-empty string"
            )
        outcome = item.get("outcome")
        if outcome not in observed:
            failures.append(f"[G4 test] tests[{index}].outcome is invalid")
        else:
            observed[outcome] += 1
    if total is not None and total != len(tests):
        failures.append("[G4 test] summary.total does not match tests length")
    if total is not None and collected is not None and collected < total:
        failures.append("[G4 test] summary.collected is below summary.total")
    for key, observed_count in observed.items():
        if counts[key] is not None and counts[key] != observed_count:
            failures.append(f"[G4 test] summary.{key} does not match test outcomes")
    if total is not None and all(value is not None for value in counts.values()):
        if sum(value or 0 for value in counts.values()) != total:
            failures.append("[G4 test] outcome counts do not add up to summary.total")
    stats.update(
        total=total or 0,
        passed=counts["passed"] or 0,
        failed=(counts["failed"] or 0) + (counts["error"] or 0),
        exitcode=exitcode if exitcode is not None else -1,
    )
    return stats


def _coverage_summary(
    value: Any, field: str, failures: list[str]
) -> tuple[float | None, dict[str, int] | None]:
    if not isinstance(value, dict):
        failures.append(f"{field} must be an object")
        return None, None
    covered = _count(value.get("covered_lines"), f"{field}.covered_lines", failures)
    statements = _count(
        value.get("num_statements"), f"{field}.num_statements", failures
    )
    missing = _count(value.get("missing_lines"), f"{field}.missing_lines", failures)
    excluded = _count(value.get("excluded_lines"), f"{field}.excluded_lines", failures)
    percentage = _finite_number(
        value.get("percent_covered"), f"{field}.percent_covered", failures, maximum=100
    )
    if None in (covered, statements, missing, excluded, percentage):
        return None, None
    assert covered is not None and statements is not None and missing is not None
    if covered + missing != statements:
        failures.append(f"{field} line counts are inconsistent")
    expected = 100.0 if statements == 0 else covered / statements * 100.0
    if not math.isclose(percentage or 0, expected, abs_tol=0.02):
        failures.append(f"{field}.percent_covered is inconsistent with line counts")
    return percentage, {
        "covered_lines": covered,
        "num_statements": statements,
        "missing_lines": missing,
        "excluded_lines": excluded or 0,
    }


def validate_coverage_report(
    data: dict[str, Any] | None, failures: list[str]
) -> tuple[float | None, dict[str, float]]:
    if data is None:
        return None, {}
    aggregate_pct, aggregate_counts = _coverage_summary(
        data.get("totals"), "[G5 coverage] totals", failures
    )
    files = data.get("files")
    if not isinstance(files, dict) or not files:
        failures.append("[G5 coverage] files must be a non-empty object")
        return aggregate_pct, {}
    modules: dict[str, float] = {}
    file_counts: list[dict[str, int]] = []
    for path, value in files.items():
        if not isinstance(path, str) or not path:
            failures.append("[G5 coverage] file path must be a non-empty string")
            continue
        if not isinstance(value, dict):
            failures.append(f"[G5 coverage] files.{path} must be an object")
            continue
        normalized = path.replace("\\", "/")
        if normalized in modules:
            failures.append(
                f"[G5 coverage] duplicate normalized file path: {normalized}"
            )
            continue
        percentage, counts = _coverage_summary(
            value.get("summary"), f"[G5 coverage] files.{normalized}.summary", failures
        )
        if percentage is not None:
            modules[normalized] = percentage
        if counts is not None:
            file_counts.append(counts)
    if aggregate_counts is not None and file_counts:
        for key in (
            "covered_lines",
            "num_statements",
            "missing_lines",
            "excluded_lines",
        ):
            if aggregate_counts[key] != sum(item[key] for item in file_counts):
                failures.append(
                    f"[G5 coverage] totals.{key} does not equal file totals"
                )
    return aggregate_pct, modules
