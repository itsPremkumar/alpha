#Requires -Version 5.1
<#
.SYNOPSIS
    Install (or remove) a local commit-msg hook enforcing Conventional Commits.

.DESCRIPTION
    Writes .git/hooks/commit-msg. Deliberately a DIFFERENT filename from the
    pre-commit framework's own "pre-commit" hook, so nothing is clobbered.

    Warn-only by default is NOT offered here - the hook either enforces or is
    removed. If it ever gets in your way, run with -Remove.

.EXAMPLE
    .\scripts\install-local-hooks.ps1
    .\scripts\install-local-hooks.ps1 -Remove
#>
[CmdletBinding()]
param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$hookPath = '.git\hooks\commit-msg'

if ($Remove) {
    if (Test-Path $hookPath) { Remove-Item $hookPath -Force; Write-Host "Removed $hookPath" -ForegroundColor Green }
    else { Write-Host 'Hook not installed.' -ForegroundColor DarkGray }
    exit 0
}

if (-not (Test-Path '.git\hooks')) { New-Item -ItemType Directory -Force -Path '.git\hooks' | Out-Null }

# Uses ONLY shell builtins (read/case/for/[) - no head/grep/sed, which are not
# guaranteed to be on PATH when Git for Windows invokes hooks from PowerShell.
$script = @'
#!/bin/sh
# Local commit-msg hook: Conventional Commits.
# Installed by scripts/install-local-hooks.ps1 - remove with -Remove.
read -r msg < "$1"
case "$msg" in
  ""|Merge*|Revert*) exit 0 ;;
esac
ok=no
for t in feat fix chore docs test refactor perf ci build revert; do
  case "$msg" in
    "$t: "*) ok=yes ;;
    "$t("?*"): "*) ok=yes ;;
  esac
done
if [ "$ok" = "yes" ]; then exit 0; fi
echo "commit-msg: subject must be Conventional Commits, e.g. fix(scope): summary"
echo "  got: $msg"
exit 1
'@

# ASCII, LF endings - the shell hook must not have a BOM or CRLF.
$bytes = [System.Text.Encoding]::ASCII.GetBytes(($script -replace "`r`n", "`n"))
[System.IO.File]::WriteAllBytes((Resolve-Path '.').Path + '\' + $hookPath, $bytes)

Write-Host "Installed $hookPath (Conventional Commits)" -ForegroundColor Green
Write-Host "Remove with: .\scripts\install-local-hooks.ps1 -Remove" -ForegroundColor DarkGray
