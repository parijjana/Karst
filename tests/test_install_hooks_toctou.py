import os
import shutil
import subprocess
from pathlib import Path

import pytest


INSTALLER = Path(__file__).parents[1] / "scripts" / "install-hooks.ps1"


def executable(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        pytest.skip(f"{name} is not available")
    return found


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


def run_installer_with_pre_upgrade_mutation(
    location: Path,
) -> subprocess.CompletedProcess[str]:
    environment = git_environment()
    environment["KARST_INSTALLER_TEST_PATH"] = str(INSTALLER)
    command = r"""
function global:Move-Item {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string]$Destination,
        [switch]$Force
    )
    if ((Split-Path -Leaf $Destination) -eq "pre-push" -and -not $Force) {
        $preCommit = Join-Path (Split-Path -Parent $Destination) "pre-commit"
        $encoding = [System.Text.UTF8Encoding]::new($false)
        [System.IO.File]::WriteAllText(
            $preCommit,
            "preserve-concurrent-change`n",
            $encoding
        )
    }
    Microsoft.PowerShell.Management\Move-Item @PSBoundParameters
}
& $env:KARST_INSTALLER_TEST_PATH
"""
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is not available")
    return subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        cwd=location,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=environment,
    )


def test_installer_revalidates_legacy_hook_immediately_before_upgrade(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    initialized = git(repository.parent, "init", str(repository))
    assert initialized.returncode == 0, initialized.stderr
    hooks_result = git(
        repository, "rev-parse", "--path-format=absolute", "--git-path", "hooks"
    )
    assert hooks_result.returncode == 0, hooks_result.stderr
    hooks = Path(hooks_result.stdout.strip())
    legacy_pre_commit = "#!/bin/bash\n\nuv run python scripts/git-pre-commit.py\n"
    legacy_post_commit = (
        "#!/bin/bash\n# Post-commit hook to log commits to code graph\n"
        "python scripts/git-post-commit.py\n"
    )
    (hooks / "pre-commit").write_text(legacy_pre_commit, encoding="utf-8")
    (hooks / "post-commit").write_text(legacy_post_commit, encoding="utf-8")

    result = run_installer_with_pre_upgrade_mutation(repository)

    assert result.returncode != 0
    assert "changed during installation" in result.stderr
    assert (hooks / "pre-commit").read_text(encoding="utf-8") == (
        "preserve-concurrent-change\n"
    )
    assert (hooks / "post-commit").read_text(encoding="utf-8") == legacy_post_commit
    assert not (hooks / "pre-push").exists()
