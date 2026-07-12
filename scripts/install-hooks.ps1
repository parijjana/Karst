$hookScript = @"
#!/bin/bash
# Pre-commit hook to update code graph
python scripts/git-pre-commit.py
"@

$hookPath = ".git/hooks/pre-commit"
New-Item -ItemType File -Force -Path $hookPath | Out-Null
Set-Content -Path $hookPath -Value $hookScript
Write-Host "Pre-commit hook installed at $hookPath"
