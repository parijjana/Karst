from __future__ import annotations

import os
from pathlib import Path

import pytest

import src.security as security
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


def test_discovery_skips_recognized_transient_generated_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    artifacts = (
        ".test-tmp-terra-review",
        ".wave2-app-green-gate-20260713-122125578",
        ".terra-recovery-tmp",
        "idx1-review-db-focused-1512",
        "parser-blockers-final-1456",
        "kgt-20260714T120000Z",
        "pytest-basetemp",
        "pytest-model55",
        "terra-rereview",
        "tmpyk_upm9l",
    )

    def walk_with_transient_artifacts(
        top: Path,
        *,
        topdown: bool,
        onerror: object,
        followlinks: bool,
    ) -> object:
        assert top == project
        assert topdown is True
        assert followlinks is False
        directories = list(artifacts)
        yield str(project), directories, []
        assert directories == []

    monkeypatch.setattr(os, "walk", walk_with_transient_artifacts)

    policy = PathSecurityPolicy((tmp_path,))

    assert policy.discover_project_files(project, {".py"}, set()) == []


def test_discovery_prunes_controlled_gate_temporary_parent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    gate_temp = project / "kgt"
    gate_temp.mkdir(parents=True)
    (gate_temp / "generated.py").write_text("x = 1", encoding="utf-8")
    source = project / "source.py"
    source.write_text("x = 1", encoding="utf-8")
    policy = PathSecurityPolicy((tmp_path,))

    discovered, untracked = policy.discover_project_inventory(project, {".py"}, set())

    assert discovered == [source]
    assert untracked == [(gate_temp, "folder")]


def test_discovery_rejects_reparse_point_named_controlled_gate_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    gate_temp = project / "kgt"
    gate_temp.mkdir(parents=True)
    policy = PathSecurityPolicy((tmp_path,))
    original_is_reparse_point = security._is_reparse_point

    def is_reparse_point(path: Path) -> bool:
        if path == gate_temp:
            return True
        return original_is_reparse_point(path)

    monkeypatch.setattr(security, "_is_reparse_point", is_reparse_point)

    with pytest.raises(SecurityViolation) as error:
        policy.discover_project_files(project, {".py"}, set())

    assert violation_code(error) == "link_not_allowed"


def test_discovery_prunes_luna_summary_pytest_temporary_parent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    temporary_parent = project / "luna-summary-pytest-elevated"
    temporary_parent.mkdir(parents=True)
    (temporary_parent / "generated.py").write_text("x = 1", encoding="utf-8")
    source = project / "source.py"
    source.write_text("x = 1", encoding="utf-8")
    policy = PathSecurityPolicy((tmp_path,))

    discovered, untracked = policy.discover_project_inventory(project, {".py"}, set())

    assert discovered == [source]
    assert untracked == [(temporary_parent, "folder")]


def test_discovery_rejects_reparse_point_named_luna_summary_pytest_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    temporary_parent = project / "luna-summary-pytest-elevated"
    temporary_parent.mkdir(parents=True)
    policy = PathSecurityPolicy((tmp_path,))
    original_is_reparse_point = security._is_reparse_point

    def is_reparse_point(path: Path) -> bool:
        if path == temporary_parent:
            return True
        return original_is_reparse_point(path)

    monkeypatch.setattr(security, "_is_reparse_point", is_reparse_point)

    with pytest.raises(SecurityViolation) as error:
        policy.discover_project_files(project, {".py"}, set())

    assert violation_code(error) == "link_not_allowed"


def test_discovery_fails_closed_for_unknown_unreadable_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    unknown = "generated-but-unrecognized"

    def walk_with_unreadable_unknown(
        top: Path,
        *,
        topdown: bool,
        onerror: object,
        followlinks: bool,
    ) -> object:
        assert top == project
        assert topdown is True
        assert followlinks is False
        directories = [unknown]
        yield str(project), directories, []
        assert directories == [unknown]
        assert callable(onerror)
        onerror(PermissionError("access denied"))

    monkeypatch.setattr(os, "walk", walk_with_unreadable_unknown)
    policy = PathSecurityPolicy((tmp_path,))

    with pytest.raises(SecurityViolation) as error:
        policy.discover_project_files(project, {".py"}, set())

    assert violation_code(error) == "path_unavailable"


def test_registered_project_success_and_public_error_contract(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    policy = PathSecurityPolicy((tmp_path,))
    identity = stable_project_id(project)

    assert validate_project_name("project-1") == "project-1"
    assert (
        validate_registered_project(policy, str(project), TRUSTED_LOCAL_OWNER, identity)
        == project
    )
    error = SecurityViolation("bounded")
    assert security_error(error) == "Security error [bounded]."

    with pytest.raises(SecurityViolation) as invalid_name:
        validate_project_name("not allowed!")
    with pytest.raises(SecurityViolation) as mismatched:
        validate_registered_project(policy, str(project), TRUSTED_LOCAL_OWNER, "wrong")
    assert violation_code(invalid_name) == "invalid_project_identifier"
    assert violation_code(mismatched) == "project_identity_invalid"
