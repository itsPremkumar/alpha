#Requires -Version 5.1
<#
.SYNOPSIS
    Snapshot the working tree (Hermes-style checkpoint) and restore on demand.

.DESCRIPTION
    Uses `git stash create`, which produces a dangling commit WITHOUT touching
    the working tree or the stash list. Nothing is modified by taking a
    checkpoint, so it is safe to run at any time.

    The commit id is written to .agent-workspace\checkpoints\latest.txt
    (already gitignored).

.EXAMPLE
    .\scripts\checkpoint.ps1                 # create
    .\scripts\checkpoint.ps1 -List           # show recorded checkpoints
    .\scripts\checkpoint.ps1 -Restore        # restore the latest one
#>
[CmdletBinding()]
param(
    [switch]$Restore,
    [switch]$List,
    [string]$Id
)

$ErrorActionPreference = 'Stop'
$dir = '.agent-workspace\checkpoints'
$latest = Join-Path $dir 'latest.txt'
$log = Join-Path $dir 'history.log'

function Ensure-Dir {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
}

if ($List) {
    if (Test-Path $log) { Get-Content $log } else { Write-Host 'No checkpoints recorded yet.' -ForegroundColor DarkGray }
    exit 0
}

if ($Restore) {
    if (-not $Id) {
        if (-not (Test-Path $latest)) { Write-Host 'No checkpoint to restore.' -ForegroundColor Yellow; exit 1 }
        $Id = (Get-Content $latest -Raw).Trim()
    }
    if (-not $Id) { Write-Host 'No checkpoint id.' -ForegroundColor Yellow; exit 1 }

    Write-Host "About to restore working tree files from checkpoint $Id" -ForegroundColor Yellow
    Write-Host 'This overwrites current modifications to TRACKED files.' -ForegroundColor Yellow
    $confirm = Read-Host 'Type RESTORE to continue'
    if ($confirm -ne 'RESTORE') { Write-Host 'Cancelled.' -ForegroundColor DarkGray; exit 0 }

    git checkout $Id -- .
    if ($LASTEXITCODE -eq 0) { Write-Host "Restored from $Id" -ForegroundColor Green } else { Write-Host 'Restore failed.' -ForegroundColor Red }
    exit $LASTEXITCODE
}

# --- create ---
Ensure-Dir
# `git stash create` prints nothing (null) when there is nothing to stash,
# so guard before calling .Trim() — PS 5.1 errors on $null.Trim().
$raw = git stash create 2>$null
$id = if ([string]::IsNullOrWhiteSpace($raw)) { '' } else { ([string]$raw).Trim() }
if (-not $id) { $id = ([string](git rev-parse HEAD)).Trim() }

Set-Content -Path $latest -Value $id
$stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
Add-Content -Path $log -Value "$stamp  $id"

Write-Host "Checkpoint created: $id" -ForegroundColor Green
Write-Host "Working tree untouched. Restore with: .\scripts\checkpoint.ps1 -Restore" -ForegroundColor DarkGray
