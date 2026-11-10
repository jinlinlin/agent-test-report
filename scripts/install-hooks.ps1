# Install git hooks: copy versioned scripts/git-hooks/* into .git/hooks/
# Usage:  powershell -ExecutionPolicy Bypass -File scripts/install-hooks.ps1
# Writes UTF-8 (no BOM) + LF so the "#!/bin/sh" shebang works under Git Bash.
# Kept ASCII-only on purpose: Windows PowerShell 5.1 reads BOM-less .ps1 as the
# local ANSI codepage, which would corrupt non-ASCII literals here.
$ErrorActionPreference = "Stop"

$repo = (git rev-parse --show-toplevel).Trim()
if (-not $repo) { throw "not a git repository" }
$hooksDir = Join-Path $repo ".git/hooks"
if (-not (Test-Path $hooksDir)) { New-Item -ItemType Directory -Path $hooksDir | Out-Null }

$srcDir = Join-Path $PSScriptRoot "git-hooks"
$enc = New-Object System.Text.UTF8Encoding($false)  # UTF-8 without BOM

Get-ChildItem -Path $srcDir -File | ForEach-Object {
    $content = [System.IO.File]::ReadAllText($_.FullName) -replace "`r`n", "`n" -replace "`r", "`n"
    $dst = Join-Path $hooksDir $_.Name
    [System.IO.File]::WriteAllText($dst, $content, $enc)
    Write-Host ("installed hook: " + $_.Name + " -> " + $dst)
}

Write-Host "done. Every future commit auto-generates a record under docs/ and adds a follow-up commit."
