import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


INSTALLER = Path(__file__).parents[1] / "scripts" / "install-hooks.ps1"


def executable(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        pytest.skip(f"{name} is not available")
    return found


def powershell_executable() -> str:
    return (
        shutil.which("pwsh")
        or shutil.which("powershell")
        or pytest.skip("PowerShell is not available")
    )


def git_environment() -> dict[str, str]:
    result = subprocess.run(
        [executable("git"), "rev-parse", "--local-env-vars"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    environment = os.environ.copy()
    for name in result.stdout.splitlines():
        environment.pop(name, None)
    return environment


def git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [executable("git"), "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=git_environment(),
    )


def initialize_repository(root: Path) -> None:
    result = git(root.parent, "init", str(root))
    assert result.returncode == 0, result.stderr


def effective_hooks_path(repository: Path) -> Path:
    result = git(
        repository, "rev-parse", "--path-format=absolute", "--git-path", "hooks"
    )
    assert result.returncode == 0, result.stderr
    return Path(result.stdout.strip())


def run_installer(location: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [powershell_executable(), "-NoProfile", "-File", str(INSTALLER)],
        cwd=location,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=git_environment(),
    )


@pytest.mark.parametrize("collision_name", ["pre-commit", "post-commit"])
@pytest.mark.parametrize("collision_kind", ["file", "directory"])
def test_installer_refuses_any_hook_collision_without_partial_install(
    tmp_path: Path, collision_name: str, collision_kind: str
) -> None:
    repository = tmp_path / "repo"
    initialize_repository(repository)
    hooks = effective_hooks_path(repository)
    collision = hooks / collision_name
    if collision_kind == "file":
        collision.write_text("preserve-me\n", encoding="utf-8")
    else:
        collision.mkdir()

    result = run_installer(repository)

    assert result.returncode != 0
    assert collision.exists()
    other_name = "post-commit" if collision_name == "pre-commit" else "pre-commit"
    assert not (hooks / other_name).exists()
    assert "Refusing to overwrite" in result.stderr


def test_installer_resolves_custom_hooks_path_from_repository_subdirectory(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    initialize_repository(repository)
    assert git(repository, "config", "core.hooksPath", ".custom-hooks").returncode == 0
    nested = repository / "one" / "two"
    nested.mkdir(parents=True)

    first = run_installer(nested)
    hooks = effective_hooks_path(repository)
    first_contents = {
        name: (hooks / name).read_bytes() for name in ["pre-commit", "post-commit"]
    }
    second = run_installer(nested)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first_contents == {
        name: (hooks / name).read_bytes() for name in first_contents
    }
    if os.name != "nt":
        assert (hooks / "pre-commit").stat().st_mode & stat.S_IXUSR


def test_installer_uses_effective_hooks_path_in_linked_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_temp = tmp_path / "git-temp"
    isolated_temp.mkdir()
    for name in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(name, str(isolated_temp))
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.setenv(name, str(tmp_path / "inherited-from-hook"))
    repository = tmp_path / "repo"
    initialize_repository(repository)
    git(repository, "config", "user.email", "gate@example.invalid")
    git(repository, "config", "user.name", "Gate Test")
    assert git(repository, "commit", "--allow-empty", "-m", "initial").returncode == 0
    worktree = tmp_path / "worktree"
    added = git(repository, "worktree", "add", "-b", "hook-test", str(worktree))
    assert added.returncode == 0, added.stderr

    result = run_installer(worktree)
    hooks = effective_hooks_path(worktree)

    assert result.returncode == 0, result.stderr
    assert (hooks / "pre-commit").is_file()
    assert (hooks / "post-commit").is_file()


def test_git_subprocess_environment_excludes_hook_local_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.setenv(name, "inherited-from-hook")

    environment = git_environment()

    assert all(
        name not in environment
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
    )


def test_installed_hooks_are_executable_by_git(tmp_path: Path) -> None:
    executable("uv")
    repository = tmp_path / "repo"
    initialize_repository(repository)
    scripts = repository / "scripts"
    scripts.mkdir()
    (scripts / "git-pre-commit.py").write_text(
        "from pathlib import Path\nPath('pre-ran').write_text('yes')\n",
        encoding="utf-8",
    )
    (scripts / "git-post-commit.py").write_text(
        "from pathlib import Path\nPath('post-ran').write_text('yes')\n",
        encoding="utf-8",
    )
    installed = run_installer(repository)
    assert installed.returncode == 0, installed.stderr

    pre = git(repository, "hook", "run", "pre-commit")
    post = git(repository, "hook", "run", "post-commit")

    assert pre.returncode == 0, pre.stderr
    assert post.returncode == 0, post.stderr
    assert (repository / "pre-ran").read_text(encoding="utf-8") == "yes"
    assert (repository / "post-ran").read_text(encoding="utf-8") == "yes"


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable mode is not available")
def test_installer_repairs_mode_of_identical_managed_hook(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    initialize_repository(repository)
    first = run_installer(repository)
    assert first.returncode == 0, first.stderr
    pre_commit = effective_hooks_path(repository) / "pre-commit"
    pre_commit.chmod(pre_commit.stat().st_mode & ~0o111)
    assert not pre_commit.stat().st_mode & stat.S_IXUSR

    second = run_installer(repository)

    assert second.returncode == 0, second.stderr
    assert pre_commit.stat().st_mode & stat.S_IXUSR
