#Requires -Version 5.1
<#
.SYNOPSIS
    One Trigger -> Action -> Deliver cycle (OpenClaw's basic automation unit).

.DESCRIPTION
    Manual by default. Nothing is scheduled unless you explicitly ask with
    -Register, and -Unregister removes it again.

    Trigger : this invocation (optionally a scheduled one).
    Action   : probe the Gateway health endpoint, plus an allowlisted command.
    Deliver  : print a summary and append to logs\trigger.log.

    Read-only: it never modifies the repo and never restarts anything.

.EXAMPLE
    .\scripts\run-trigger.ps1
    .\scripts\run-trigger.ps1 -Action "make doctor"
    .\scripts\run-trigger.ps1 -Register -Hourly
    .\scripts\run-trigger.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [string]$Action,
    [string]$GatewayUrl = 'http://127.0.0.1:8001/health/ready',
    [switch]$Register,
    [switch]$Hourly,
    [switch]$Unregister
)

$ErrorActionPreference = 'Stop'
$root   = Split-Path $PSScriptRoot -Parent
$task   = 'AgentWorkspaceTrigger'
$logDir = Join-Path $root 'logs'
$log    = Join-Path $logDir 'trigger.log'

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }

# --- Register / Unregister (explicitly opt-in) ---
if ($Unregister) {
    schtasks /Delete /TN $task /F 2>&1 | Out-Null
    Write-Host "Removed scheduled task '$task'." -ForegroundColor Green
    exit 0
}

if ($Register) {
    $schedule = if ($Hourly) { 'HOURLY' } else { 'DAILY' }
    $cmd = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$root\scripts\run-trigger.ps1`""
    schtasks /Create /TN $task /TR $cmd /SC $schedule /F 2>&1 | Out-Null
    Write-Host "Registered '$task' ($schedule). Remove with -Unregister." -ForegroundColor Green
    exit 0
}

# --- Trigger -> Action -> Deliver ---
$stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
$lines = @("===== $stamp =====")

# Action 1: Gateway health
$health = 'unknown'
try {
    $r = Invoke-RestMethod -Uri $GatewayUrl -TimeoutSec 8
    $health = if ($r.status) { "$($r.status)" } else { 'ok' }
    $lines += "gateway: $health"
} catch {
    $health = 'unreachable'
    $lines += "gateway: unreachable ($($_.Exception.Message))"
}

# Action 2: optional allowlisted command (reuse the safe-exec gate)
if ($Action) {
    $allowed = $Action -match '^(uv|pnpm|node|git (status|log|diff|show)|make (doctor|check|help|verify))($| )'
    if (-not $allowed) {
        $lines += "action: REFUSED (not allowlisted): $Action"
    } else {
        $out = & cmd.exe /c $Action 2>&1 | Select-Object -First 10 | Out-String
        $lines += "action: $Action"
        $lines += $out.Trim()
    }
}

# Deliver
$lines += ''
$body = $lines -join "`n"
Write-Host $body
Add-Content -Path $log -Value $body

if ($health -eq 'unreachable') { exit 1 }
exit 0
