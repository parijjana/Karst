import json
from typing import Any


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value} is forbidden")


def _location(value: Any, field: str, failures: list[str]) -> bool:
    if not isinstance(value, dict):
        failures.append(f"{field} must be an object")
        return False
    valid = True
    for coordinate in ("row", "column"):
        number = value.get(coordinate)
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            failures.append(f"{field}.{coordinate} must be a positive integer")
            valid = False
    return valid


def _optional_types(item: dict[str, Any], index: int, failures: list[str]) -> bool:
    valid = True
    if "url" in item and item["url"] is not None and not isinstance(item["url"], str):
        failures.append(f"[G1 ruff] items[{index}].url must be a string or null")
        valid = False
    for field in ("cell", "noqa_row"):
        value = item.get(field)
        if (
            field in item
            and value is not None
            and (isinstance(value, bool) or not isinstance(value, int) or value < 0)
        ):
            failures.append(
                f"[G1 ruff] items[{index}].{field} must be a non-negative integer or null"
            )
            valid = False
    if "end_location" in item and item["end_location"] is not None:
        valid = (
            _location(
                item["end_location"], f"[G1 ruff] items[{index}].end_location", failures
            )
            and valid
        )
    fix = item.get("fix")
    if "fix" not in item or fix is None:
        return valid
    if not isinstance(fix, dict):
        failures.append(f"[G1 ruff] items[{index}].fix must be an object or null")
        return False
    if not isinstance(fix.get("applicability"), str):
        failures.append(f"[G1 ruff] items[{index}].fix.applicability must be a string")
        valid = False
    if not isinstance(fix.get("message"), str):
        failures.append(f"[G1 ruff] items[{index}].fix.message must be a string")
        valid = False
    edits = fix.get("edits")
    if not isinstance(edits, list):
        failures.append(f"[G1 ruff] items[{index}].fix.edits must be an array")
        return False
    for edit_index, edit in enumerate(edits):
        field = f"[G1 ruff] items[{index}].fix.edits[{edit_index}]"
        if not isinstance(edit, dict):
            failures.append(f"{field} must be an object")
            valid = False
            continue
        if not isinstance(edit.get("content"), str):
            failures.append(f"{field}.content must be a string")
            valid = False
        valid = _location(edit.get("location"), f"{field}.location", failures) and valid
        valid = (
            _location(edit.get("end_location"), f"{field}.end_location", failures)
            and valid
        )
    return valid


def validate_ruff_output(stdout: str, failures: list[str]) -> list[str]:
    if not stdout.strip():
        failures.append("[G1 ruff] output must contain a JSON list")
        return []
    try:
        data = json.loads(stdout, parse_constant=_reject_nonfinite_json)
    except (ValueError, json.JSONDecodeError) as exc:
        failures.append(f"[G1 ruff] output is invalid JSON: {exc}")
        return []
    if not isinstance(data, list):
        failures.append("[G1 ruff] output root must be a JSON list")
        return []
    findings: list[str] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            failures.append(f"[G1 ruff] items[{index}] must be an object")
            continue
        valid = True
        for field in ("code", "filename", "message"):
            if not isinstance(item.get(field), str) or not item[field]:
                failures.append(
                    f"[G1 ruff] items[{index}].{field} must be a non-empty string"
                )
                valid = False
        valid = (
            _location(
                item.get("location"), f"[G1 ruff] items[{index}].location", failures
            )
            and valid
        )
        valid = _optional_types(item, index, failures) and valid
        if valid:
            location = item["location"]
            findings.append(
                f"[G1 ruff] {item['filename']}:{location['row']} "
                f"{item['message']} ({item['code']})"
            )
    return findings
