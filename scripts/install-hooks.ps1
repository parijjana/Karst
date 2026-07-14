$ErrorActionPreference = "Stop"

$managedHooks = [ordered]@{
    "pre-commit" = @"
#!/bin/bash
# Managed by Karst scripts/install-hooks.ps1
# Pre-commit hook to update code graph
uv run python scripts/git-pre-commit.py
"@
    "post-commit" = @"
#!/bin/bash
# Managed by Karst scripts/install-hooks.ps1
# Post-commit hook to log commits to code graph
uv run python scripts/git-post-commit.py
"@
}

function Invoke-GitText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = & git @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed: $($output -join [Environment]::NewLine)"
    }
    return ($output -join [Environment]::NewLine).Trim()
}

function Get-PathObject {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    return Get-Item -LiteralPath $LiteralPath -Force -ErrorAction SilentlyContinue
}

$createdHooksDirectory = $false
$hooksDirectory = $null
$stagedPaths = [System.Collections.Generic.List[string]]::new()
$installedPaths = [System.Collections.Generic.List[string]]::new()

try {
    $inside = Invoke-GitText -Arguments @("rev-parse", "--is-inside-work-tree")
    if ($inside -ne "true") {
        throw "Current directory is not inside a Git worktree."
    }
    $hooksText = Invoke-GitText -Arguments @(
        "rev-parse", "--path-format=absolute", "--git-path", "hooks"
    )
    $hooksDirectory = [System.IO.Path]::GetFullPath($hooksText)
    $hooksObject = Get-PathObject -LiteralPath $hooksDirectory
    if ($null -ne $hooksObject -and -not $hooksObject.PSIsContainer) {
        throw "Refusing to use hooks path because it is not a directory: $hooksDirectory"
    }
    if ($null -eq $hooksObject) {
        New-Item -ItemType Directory -Path $hooksDirectory -Force | Out-Null
        $createdHooksDirectory = $true
    }

    $pendingNames = [System.Collections.Generic.List[string]]::new()
    $existingManagedPaths = [System.Collections.Generic.List[string]]::new()
    foreach ($name in $managedHooks.Keys) {
        $hookPath = Join-Path $hooksDirectory $name
        $existing = Get-PathObject -LiteralPath $hookPath
        if ($null -ne $existing) {
            if ($existing.PSIsContainer) {
                throw "Refusing to overwrite existing hook object: $hookPath"
            }
            $existingContent = [System.IO.File]::ReadAllText($hookPath)
            if ($existingContent -ne $managedHooks[$name]) {
                throw "Refusing to overwrite existing hook: $hookPath"
            }
            $existingManagedPaths.Add($hookPath)
        }
        else {
            $pendingNames.Add($name)
        }
    }

    $utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
    $isWindowsPlatform = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
    if (-not $isWindowsPlatform) {
        foreach ($hookPath in $existingManagedPaths) {
            & chmod "+x" "--" $hookPath
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to repair executable mode for managed hook: $hookPath"
            }
        }
    }
    $stagedByName = @{}
    foreach ($name in $pendingNames) {
        $stagePath = Join-Path $hooksDirectory ".$name.karst-stage-$([guid]::NewGuid().ToString('N'))"
        [System.IO.File]::WriteAllText($stagePath, $managedHooks[$name], $utf8WithoutBom)
        $stagedPaths.Add($stagePath)
        if (-not $isWindowsPlatform) {
            & chmod "+x" "--" $stagePath
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to mark staged hook executable: $stagePath"
            }
        }
        $stagedByName[$name] = $stagePath
    }

    foreach ($name in $pendingNames) {
        $hookPath = Join-Path $hooksDirectory $name
        if ($null -ne (Get-PathObject -LiteralPath $hookPath)) {
            throw "Refusing to overwrite hook created during installation: $hookPath"
        }
        $stagePath = $stagedByName[$name]
        Move-Item -LiteralPath $stagePath -Destination $hookPath -ErrorAction Stop
        $stagedPaths.Remove($stagePath) | Out-Null
        $installedPaths.Add($hookPath)
    }

    foreach ($name in $managedHooks.Keys) {
        $hookPath = Join-Path $hooksDirectory $name
        if ($installedPaths.Contains($hookPath)) {
            Write-Host "Installed managed hook at $hookPath"
        }
        else {
            Write-Host "Managed hook already installed at $hookPath"
        }
    }
}
catch {
    foreach ($path in $installedPaths) {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    }
    foreach ($path in $stagedPaths) {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    }
    if ($createdHooksDirectory -and $null -ne $hooksDirectory -and (Test-Path -LiteralPath $hooksDirectory -PathType Container)) {
        $children = Get-ChildItem -LiteralPath $hooksDirectory -Force -ErrorAction SilentlyContinue
        if ($null -eq $children) {
            Remove-Item -LiteralPath $hooksDirectory -Force -ErrorAction SilentlyContinue
        }
    }
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
