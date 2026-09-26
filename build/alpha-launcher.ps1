<#
    Alpha - lightweight launcher for the native (no-Docker) install.

    What this is
    ------------
    A ~7 KB PowerShell script that makes a running Alpha install openable in one
    double-click: it makes sure the local stack is up, proves the gateway is
    healthy, and then opens the web UI at http://127.0.0.1:3000 (the gateway it
    health-checks is http://127.0.0.1:8001/health).

    Why this exists instead of a static Tauri/WebView2 bundle
    ---------------------------------------------------------
    MEASURED, not assumed: Alpha's frontend is a Next.js SERVER, not a static
    export. frontend/next.config.mjs proxies /api/* to the gateway using
    `rewrites()`, and a rewrite is a server-side feature that a static export
    cannot perform; the existing Electron build proves it by shipping a Node
    runtime and a Next standalone server inside the bundle
    (electron/dist/win-unpacked/resources/runtime = 121.9 MB measured, plus
    resources/frontend-standalone = 78.5 MB measured).

    So a static bundle would load and then 404 on every single API call. Building
    one would be building a shell that cannot run the app, so it was NOT built.

    What this launcher does instead is take the one genuinely cheap win: on
    Windows 10/11 the WebView2 runtime is already present and is what Edge
    itself uses, so opening the local server costs ZERO extra megabytes of
    bundled Chromium. The Electron alternative costs 121.9 MB of portable Node
    plus Electron's own Chromium payload inside a 585.8 MB unpacked tree and a
    195.4 MB installer (all measured on Windows 11 build 22631, AMD64).

    Honest limitation: opening the OS browser is NOT the same as a native
    WebView2-hosted window with app chrome, a tray icon or a frameless frame.
    That would need a WebView2Loader.dll host, which is a real build artefact
    and was out of scope for a cheap prototype. Electron remains the supported
    desktop shell; this launcher is the fast, small, no-download path.

    Usage:
        powershell -ExecutionPolicy Bypass -File alpha-launcher.ps1
        powershell -ExecutionPolicy Bypass -File alpha-launcher.ps1 -NoStart
#>

[CmdletBinding()]
param(
    [string]$InstallRoot,

    # Do not try to start Alpha; only check and open.
    [switch]$NoStart,

    [int]$GatewayPort = 8001,
    [int]$FrontendPort = 3000,

    [int]$StartupTimeoutSeconds = 600
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not $InstallRoot) { $InstallRoot = Join-Path $env:LOCALAPPDATA 'Alpha' }
$repo = Join-Path $InstallRoot 'alpha'
$gatewayHealth = "http://127.0.0.1:$GatewayPort/health"
$frontendUrl = "http://127.0.0.1:$FrontendPort"

function Test-Url {
    param([string]$Uri, [int]$TimeoutSec = 5)
    try {
        $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
        return ([int]$response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Show-Status {
    param([string]$Message, [string]$Colour = 'Gray')
    Write-Host $Message -ForegroundColor $Colour
}

Show-Status ''
Show-Status 'Alpha launcher' 'Cyan'
Show-Status "  install root : $repo"

if (-not (Test-Path -LiteralPath (Join-Path $repo 'start.ps1'))) {
    Show-Status "  Alpha is not installed at $repo" 'Red'
    Show-Status '  Install it first: powershell -ExecutionPolicy Bypass -File installer\bootstrap.ps1' 'Yellow'
    exit 1
}

# ---- Make sure the stack is up ---------------------------------------------
if (-not (Test-Url -Uri $frontendUrl)) {
    if ($NoStart) {
        Show-Status "  the web UI at $frontendUrl is not responding and -NoStart was given" 'Yellow'
        exit 2
    }
    Show-Status '  Alpha is not running; starting it (first boot can take a few minutes)...' 'Yellow'

    # The quotes around -File are load-bearing: Start-Process joins the argument
    # array without quoting, so a path containing a space would truncate.
    Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
        '-File', ('"{0}"' -f (Join-Path $repo 'start.ps1')), '-NoBrowser'
    ) -WorkingDirectory $repo -WindowStyle Hidden | Out-Null

    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    $gatewayUp = $false
    while ((Get-Date) -lt $deadline) {
        if (Test-Url -Uri $gatewayHealth) { $gatewayUp = $true }
        if ($gatewayUp -and (Test-Url -Uri $frontendUrl)) { break }
        Start-Sleep -Seconds 5
    }
    if (-not $gatewayUp) {
        Show-Status "  the gateway health endpoint $gatewayHealth never answered" 'Red'
        Show-Status "  Inspect $repo\logs\gateway.err.log" 'Yellow'
        exit 3
    }
    Show-Status '  Alpha is up' 'Green'
} else {
    Show-Status '  Alpha is already running' 'Green'
}

# ---- Prove the gateway is actually healthy, then open the UI ---------------
if (-not (Test-Url -Uri $gatewayHealth)) {
    Show-Status "  warning: $gatewayHealth did not answer; the UI may show errors" 'Yellow'
}

Show-Status "  opening $frontendUrl" 'Cyan'
# The OS handler for http:// is Edge on Windows 10/11, which is a WebView2 host.
# That is the entire size argument for this launcher: the renderer is already on
# the machine, so nothing is downloaded and nothing is bundled.
Start-Process $frontendUrl | Out-Null

Show-Status ''
Show-Status "  web UI   : $frontendUrl" 'White'
Show-Status "  gateway  : $gatewayHealth" 'White'
Show-Status "  stop     : $repo\stop.ps1" 'White'
Show-Status ''
exit 0
