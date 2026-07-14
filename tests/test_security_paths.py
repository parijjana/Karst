from __future__ import annotations

from pathlib import Path

import pytest

from src.security import (
    PathSecurityPolicy,
    SecurityViolation,
    security_error,
    stable_project_id,
    validate_project_name,
    validate_registered_project,
)
from src.settings import TRUSTED_LOCAL_OWNER


def violation_code(error: pytest.ExceptionInfo[SecurityViolation]) -> str:
    return error.value.code


def test_policy_constructor_and_project_root_rejections(tmp_path: Path) -> None:
    with pytest.raises(SecurityViolation) as empty:
        PathSecurityPolicy(())
    with pytest.raises(SecurityViolation) as unavailable:
        PathSecurityPolicy((tmp_path / "missing",))
    assert violation_code(empty) == "allowed_roots_unavailable"
    assert violation_code(unavailable) == "allowed_roots_unavailable"

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    source = allowed / "source.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    policy = PathSecurityPolicy((allowed,))
    with pytest.raises(SecurityViolation) as relative:
        policy.validate_project_root("relative")
    with pytest.raises(SecurityViolation) as file_root:
        policy.validate_project_root(source)
    with pytest.raises(SecurityViolation) as outside:
        policy.validate_project_root(tmp_path)

    assert violation_code(relative) == "absolute_path_required"
    assert violation_code(file_root) == "project_root_invalid"
    assert violation_code(outside) == "path_not_allowed"


def test_project_file_validation_covers_success_and_fail_closed_paths(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    project = allowed / "project"
    outside = tmp_path / "outside"
    project.mkdir(parents=True)
    outside.mkdir()
    source = project / "source.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    policy = PathSecurityPolicy((allowed,))

    assert policy.validate_project_file("source.py", project) == source
    with pytest.raises(SecurityViolation) as missing_root:
        policy.validate_project_file(source, project / "missing")
    with pytest.raises(SecurityViolation) as outside_root:
        policy.validate_project_file(source, outside)
    with pytest.raises(SecurityViolation) as directory:
        policy.validate_project_file(project, project)
    with pytest.raises(SecurityViolation) as missing_file:
        policy.validate_project_file(project / "missing.py", project)

    assert violation_code(missing_root) == "project_identity_invalid"
    assert violation_code(outside_root) == "project_boundary_violation"
    assert violation_code(directory) == "project_file_invalid"
    assert violation_code(missing_file) == "path_unavailable"


def test_discovery_filters_hidden_ignored_and_unsupported_entries(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    hidden = project / ".hidden"
    ignored = project / "build"
    nested = project / "nested"
    for directory in (hidden, ignored, nested):
        directory.mkdir(parents=True)
    (hidden / "hidden.py").write_text("x = 1", encoding="utf-8")
    (ignored / "built.py").write_text("x = 1", encoding="utf-8")
    (project / "notes.txt").write_text("ignored", encoding="utf-8")
    source = nested / "source.py"
    source.write_text("x = 1", encoding="utf-8")
    policy = PathSecurityPolicy((tmp_path,))

    discovered = policy.discover_project_files(project, {".py"}, {"build"})

    assert discovered == [source]


def test_registered_project_success_and_public_error_contract(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    policy = PathSecurityPolicy((tmp_path,))
    identity = stable_project_id(project)

    assert validate_project_name("project-1") == "project-1"
    assert validate_registered_project(
        policy, str(project), TRUSTED_LOCAL_OWNER, identity
    ) == project
    error = SecurityViolation("bounded")
    assert security_error(error) == "Security error [bounded]."

    with pytest.raises(SecurityViolation) as invalid_name:
        validate_project_name("not allowed!")
    with pytest.raises(SecurityViolation) as mismatched:
        validate_registered_project(
            policy, str(project), TRUSTED_LOCAL_OWNER, "wrong"
        )
    assert violation_code(invalid_name) == "invalid_project_identifier"
    assert violation_code(mismatched) == "project_identity_invalid"
