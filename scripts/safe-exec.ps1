#Requires -Version 5.1
<#
.SYNOPSIS
    Run a whitelisted command only. Deny-by-default.

.DESCRIPTION
    An exec-approval gate in the OpenClaw sense: capability is granted
    explicitly, never inferred. Anything not matching the allowlist is refused.

    - Dry-run is the DEFAULT (nothing executes without -Apply).
    - Destructive verbs (rm, del, remove-item, taskkill /F, format, wmic) are
      refused outright, even inside an otherwise-allowed command.
    - No arbitrary string is ever passed to Invoke-Expression.

.EXAMPLE
    .\scripts\safe-exec.ps1 git status
    .\scripts\safe-exec.ps1 git status -Apply
    .\scripts\safe-exec.ps1 "rm -rf x"          # refused
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [string]$Command,

    [switch]$Apply
)

$ErrorActionPreference = 'Stop'

# Commands that are always refused, regardless of allowlist.
$forbidden = @(
    'rm ', 'rm.exe', 'rmdir', 'del ', 'del.exe', 'remove-item', 'ri ', 'rd ',
    'taskkill', 'stop-process', 'format', 'wmic', 'diskpart',
    'git clean', 'git reset --hard', 'git push --force', 'git push -f',
    '--force', '-Force'
)

# Allowlist: anchored regexes. Keep this list small and boring.
$allowlist = @(
    '^uv($| )',
    '^pnpm($| )',
    '^node($| )',
    '^git (status|log|diff|show|branch|rev-parse|config --local --get)($| )',
    '^make (doctor|check|help|verify)($| )',
    '^python scripts/(check|doctor)\.py($| )'
)

$trimmed = $Command.Trim()

foreach ($bad in $forbidden) {
    if ($trimmed -match [regex]::Escape($bad)) {
        Write-Host "REFUSED (destructive/forbidden token '$bad'): $trimmed" -ForegroundColor Red
        exit 2
    }
}

$allowed = $false
foreach ($pattern in $allowlist) {
    if ($trimmed -match $pattern) { $allowed = $true; break }
}

if (-not $allowed) {
    Write-Host "REFUSED (not on allowlist): $trimmed" -ForegroundColor Red
    Write-Host "Allowlist: uv, pnpm, node, git (status|log|diff|show|branch|rev-parse|config --local --get), make (doctor|check|help|verify)" -ForegroundColor DarkGray
    exit 1
}

if (-not $Apply) {
    Write-Host "DRY-RUN (re-run with -Apply to execute): $trimmed" -ForegroundColor Yellow
    exit 0
}

Write-Host "EXEC: $trimmed" -ForegroundColor Cyan
& cmd.exe /c $trimmed
exit $LASTEXITCODE
