$ErrorActionPreference = "Stop"

$managedHooks = [ordered]@{
    "pre-commit" = @"
#!/bin/bash
# Managed by Karst scripts/install-hooks.ps1
# Pre-commit hook to update code graph
uv run python scripts/git-pre-commit.py
"@
    "pre-push" = @"
#!/bin/bash
# Managed by Karst scripts/install-hooks.ps1
# Pre-push hook to block non-green code from reaching the remote
uv run python scripts/gate.py --timeout-seconds 300
"@
    "post-commit" = @"
#!/bin/bash
# Managed by Karst scripts/install-hooks.ps1
# Post-commit hook to log commits to code graph
uv run python scripts/git-post-commit.py
"@
}

$legacyManagedHooks = @{
    "pre-commit" = @(
        "#!/bin/bash`n`nuv run python scripts/git-pre-commit.py`n"
    )
    "post-commit" = @(
        "#!/bin/bash`n# Post-commit hook to log commits to code graph`npython scripts/git-post-commit.py`n"
    )
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

function ConvertTo-NormalizedHookText {
    param([Parameter(Mandatory = $true)][string]$Value)
    return $Value.Replace("`r`n", "`n").TrimEnd([char[]]"`r`n")
}

function Test-LegacyManagedHook {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Content
    )
    if (-not $legacyManagedHooks.ContainsKey($Name)) {
        return $false
    }
    $normalized = ConvertTo-NormalizedHookText -Value $Content
    foreach ($legacyContent in $legacyManagedHooks[$Name]) {
        if ($normalized -ceq (ConvertTo-NormalizedHookText -Value $legacyContent)) {
            return $true
        }
    }
    return $false
}

$createdHooksDirectory = $false
$hooksDirectory = $null
$stagedPaths = [System.Collections.Generic.List[string]]::new()
$installedPaths = [System.Collections.Generic.List[string]]::new()
$upgradedPaths = [System.Collections.Generic.List[string]]::new()
$originalContentByName = @{}
$utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)

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
    $upgradeNames = [System.Collections.Generic.List[string]]::new()
    $existingManagedPaths = [System.Collections.Generic.List[string]]::new()
    foreach ($name in $managedHooks.Keys) {
        $hookPath = Join-Path $hooksDirectory $name
        $existing = Get-PathObject -LiteralPath $hookPath
        if ($null -ne $existing) {
            if ($existing.PSIsContainer) {
                throw "Refusing to overwrite existing hook object: $hookPath"
            }
            $existingContent = [System.IO.File]::ReadAllText($hookPath)
            $normalizedExisting = ConvertTo-NormalizedHookText -Value $existingContent
            $normalizedManaged = ConvertTo-NormalizedHookText -Value $managedHooks[$name]
            if ($normalizedExisting -ceq $normalizedManaged) {
                $existingManagedPaths.Add($hookPath)
            }
            elseif (Test-LegacyManagedHook -Name $name -Content $existingContent) {
                $upgradeNames.Add($name)
                $originalContentByName[$name] = $existingContent
            }
            else {
                throw "Refusing to overwrite existing hook: $hookPath"
            }
        }
        else {
            $pendingNames.Add($name)
        }
    }

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
    $namesToStage = [System.Collections.Generic.List[string]]::new()
    foreach ($name in $pendingNames) {
        $namesToStage.Add($name)
    }
    foreach ($name in $upgradeNames) {
        $namesToStage.Add($name)
    }
    foreach ($name in $namesToStage) {
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

    foreach ($name in $upgradeNames) {
        $hookPath = Join-Path $hooksDirectory $name
        $current = Get-PathObject -LiteralPath $hookPath
        if ($null -eq $current -or $current.PSIsContainer) {
            throw "Legacy hook object changed during installation: $hookPath"
        }
        $currentContent = [System.IO.File]::ReadAllText($hookPath)
        if ($currentContent -cne [string]$originalContentByName[$name]) {
            throw "Legacy hook content changed during installation: $hookPath"
        }
        $stagePath = $stagedByName[$name]
        Move-Item -LiteralPath $stagePath -Destination $hookPath -Force -ErrorAction Stop
        $stagedPaths.Remove($stagePath) | Out-Null
        $upgradedPaths.Add($hookPath)
    }

    foreach ($name in $managedHooks.Keys) {
        $hookPath = Join-Path $hooksDirectory $name
        if ($installedPaths.Contains($hookPath)) {
            Write-Host "Installed managed hook at $hookPath"
        }
        elseif ($upgradedPaths.Contains($hookPath)) {
            Write-Host "Upgraded managed hook at $hookPath"
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
    foreach ($path in $upgradedPaths) {
        $name = Split-Path -Leaf $path
        $current = Get-PathObject -LiteralPath $path
        if ($null -ne $current -and -not $current.PSIsContainer) {
            $currentContent = [System.IO.File]::ReadAllText($path)
            $managedContent = [string]$managedHooks[$name]
            if ($currentContent -ceq $managedContent) {
                [System.IO.File]::WriteAllText(
                    $path,
                    [string]$originalContentByName[$name],
                    $utf8WithoutBom
                )
            }
        }
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
