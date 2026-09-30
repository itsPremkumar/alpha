<#
.SYNOPSIS
    Start the Alpha frontend for DEVELOPMENT, with hot reload and no production build.

.DESCRIPTION
    `next build` on this frontend takes ~10 minutes. That is the right cost for a
    release and the wrong cost for editing a component, because you pay it again
    for every change. This script starts `next dev` instead: one cold compile when
    the server starts, then hot reload in about a second per edit.

    It is idempotent. If something is already serving port 3000 it says so and
    exits 0 rather than starting a second server that fights for the port.

    The cold compile is reported as STARTING, never as a failure. This matters:
    the first response took ~390s here and ~880s on a cold cache, and a probe
    with a short timeout reports that as DOWN, which sends you looking for a
    crash that never happened. `scripts/stack_doctor.py` exists for the same
    reason.

.PARAMETER Wait
    Seconds to wait for the first response. Default 1500, because the measured
    cold compile is longer than any timeout anyone would pick by hand.

.EXAMPLE
    pwsh scripts/dev_ui.ps1
    pwsh scripts/dev_ui.ps1 -Wait 3000
#>
[CmdletBinding()]
param(
    [int]$Wait = 1500,
    [int]$Port = 3000
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $repo 'frontend'
$cli = Join-Path $frontend 'node_modules/next/dist/bin/next'
$logDir = Join-Path $env:TEMP 'opencode'
$log = Join-Path $logDir 'nextdev.log'
$err = Join-Path $logDir 'nextdev.err'

if (-not (Test-Path $cli)) {
    throw "next CLI missing at $cli - run 'make install' (or pnpm install in frontend/) first."
}

# Already serving? Do not start a second server.
try {
    $r = Invoke-WebRequest "http://127.0.0.1:$Port" -TimeoutSec 5 -UseBasicParsing
    if ($r.StatusCode -eq 200) {
        Write-Host "frontend already serving on $Port (HTTP $($r.StatusCode)) - nothing to do." -ForegroundColor Green
        Write-Host "Edit any file under frontend/src and it reloads automatically."
        exit 0
    }
} catch {
    # Nothing there yet, or still compiling. Either way we fall through and start.
}

# Clear the dev distDir only when we are genuinely starting cold. A warm start is
# what makes the second run fast; leaving .next/dev in place is the point.
$devDir = Join-Path $frontend '.next/dev'
if (Test-Path $devDir) {
    Write-Host "keeping warm dev cache at .next/dev" -ForegroundColor DarkGray
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Write-Host "starting next dev on $Port (hot reload, no build)..." -ForegroundColor Cyan
$proc = Start-Process -FilePath 'node' `
    -ArgumentList $cli, 'dev', '-p', $Port, '-H', '127.0.0.1' `
    -WorkingDirectory $frontend `
    -RedirectStandardOutput $log -RedirectStandardError $err `
    -WindowStyle Hidden -PassThru

Write-Host "pid $($proc.Id). Compiling the first response; this is slow ONCE, then edits are ~1s."
Write-Host "log: $log"
Write-Host ""

$sw = [Diagnostics.Stopwatch]::StartNew()
$deadline = $Wait
while ($sw.Elapsed.TotalSeconds -lt $deadline) {
    Start-Sleep -Seconds 10
    try {
        $resp = Invoke-WebRequest "http://127.0.0.1:$Port" -TimeoutSec 20 -UseBasicParsing
        if ($resp.StatusCode -eq 200) {
            Write-Host ("READY in {0:N0}s - open http://127.0.0.1:{1}" -f $sw.Elapsed.TotalSeconds, $Port) -ForegroundColor Green
            Write-Host "Full stack: http://127.0.0.1:2026  (nginx proxies here)"
            exit 0
        }
    } catch {
        $elapsed = [Math]::Round($sw.Elapsed.TotalSeconds)
        if ($elapsed % 60 -lt 10) {
            Write-Host "  ${elapsed}s - still compiling (this is STARTING, not a failure)" -ForegroundColor DarkGray
        }
    }
}

Write-Host "NOT READY after ${deadline}s. The process may still be compiling." -ForegroundColor Yellow
Write-Host "This is not proof of a crash. Check the real output:" -ForegroundColor Yellow
Write-Host "  Get-Content '$log' -Tail 40"
Write-Host "  Get-Content '$err' -Tail 40"
exit 1
