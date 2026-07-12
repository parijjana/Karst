$hookScript = @"
#!/bin/bash
# Pre-commit hook to update code graph
python scripts/git-pre-commit.py
"@

$hookPath = ".git/hooks/pre-commit"
New-Item -ItemType File -Force -Path $hookPath | Out-Null
Set-Content -Path $hookPath -Value $hookScript
Write-Host "Pre-commit hook installed at $hookPath"

$postHookScript = @"
#!/bin/bash
# Post-commit hook to log commits to code graph
python scripts/git-post-commit.py
"@

$postHookPath = ".git/hooks/post-commit"
New-Item -ItemType File -Force -Path $postHookPath | Out-Null
Set-Content -Path $postHookPath -Value $postHookScript
Write-Host "Post-commit hook installed at $postHookPath"
