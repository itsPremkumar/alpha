# Alpha - One-Click Installer for Windows (PowerShell)
# Usage: .\install.ps1

[CmdletBinding()]
param (
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
Set-Location $RepoRoot

# Windows resolves a bare command name through PATHEXT, and PowerShell refuses
# to execute an .exe at all ("Cannot run a document in the middle of a pipeline")
# when PATHEXT has been narrowed -- some managed images ship PATHEXT=.CPL. When
# that happens `uv` and `node` are neither resolvable nor launchable even though
# both are installed and on PATH. Restore the platform default.
if (-not $env:PATHEXT -or $env:PATHEXT -notlike "*.EXE*") {
    $env:PATHEXT = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC;.CPL"
}

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host "    Alpha - Automated Setup & Installation    " -ForegroundColor Cyan
Write-Host "========================================================`n" -ForegroundColor Cyan

# 1. Check & locate uv
Write-Host "[1/5] Checking Python / uv package manager..." -ForegroundColor Yellow
# Resolving a bare name through PATHEXT is not reliable: when PATHEXT is missing
# ".EXE" (or the tool is not on PATH at all), `Get-Command uv` returns nothing
# even though uv.exe exists and its directory is on PATH. Resolve "<name>.exe"
# explicitly, then fall back to the known install locations.
function Resolve-Executable {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$ExtraCandidates = @()
    )
    foreach ($candidate in @("$Name.exe", $Name)) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    foreach ($c in $ExtraCandidates) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    foreach ($root in @($env:LOCALAPPDATA, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not $root) { continue }
        try {
            $hit = Get-ChildItem -Path $root -Filter "$Name.exe" -Recurse -Depth 2 -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($hit) { return $hit.FullName }
        } catch {}
    }
    return $null
}

$uvCandidates = @(
    "$env:USERPROFILE\.cargo\bin\uv.exe",
    "$env:APPDATA\uv\uv.exe",
    "$env:LOCALAPPDATA\Programs\uv\uv.exe",
    "$env:LOCALAPPDATA\hermes\bin\uv.exe",
    "$env:USERPROFILE\.local\bin\uv.exe",
    "$env:USERPROFILE\scoop\shims\uv.exe",
    "$env:ProgramData\chocolatey\bin\uv.exe"
)
$uvPath = Resolve-Executable -Name "uv" -ExtraCandidates $uvCandidates

if (-not $uvPath) {
    Write-Host "  -> 'uv' not found. Installing Astral uv automatically..." -ForegroundColor Yellow
    try {
        powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
        $env:PATH = ("$env:USERPROFILE\.cargo\bin;" + $env:PATH)
        $uvPath = Resolve-Executable -Name "uv" -ExtraCandidates $uvCandidates
    } catch {
        Write-Error "Failed to auto-install uv. Please install it manually from https://astral.sh/uv and retry."
        exit 1
    }
}
if (-not $uvPath) {
    Write-Host "[ERROR] Could not locate 'uv' even after attempting installation." -ForegroundColor Red
    Write-Host "Install it from https://astral.sh/uv and re-run this script.`n" -ForegroundColor Yellow
    exit 1
}
$env:PATH = ((Split-Path $uvPath) + ";" + $env:PATH)
$uvVersion = & $uvPath --version
Write-Host "  [OK] $uvVersion" -ForegroundColor Green

# 2. Check Node.js
Write-Host "`n[2/5] Checking Node.js runtime..." -ForegroundColor Yellow
$nodePath = Resolve-Executable -Name "node" -ExtraCandidates @(
    "$env:ProgramFiles\nodejs\node.exe",
    "${env:ProgramFiles(x86)}\nodejs\node.exe",
    "$env:LOCALAPPDATA\Programs\nodejs\node.exe",
    "$env:APPDATA\nvm\v22.22.2\node.exe",
    "$env:LOCALAPPDATA\nvm4w\nodejs\node.exe"
)
if (-not $nodePath) {
    Write-Error "Node.js (version 22+) is required. Please install it from https://nodejs.org/ and rerun this script."
    exit 1
}
$env:PATH = ((Split-Path $nodePath) + ";" + $env:PATH)
$nodeVersion = & $nodePath -v
Write-Host "  [OK] Node.js $nodeVersion" -ForegroundColor Green

# 3. Setup configuration files
Write-Host "`n[3/5] Setting up configuration files..." -ForegroundColor Yellow

# .env
if (-not (Test-Path "$RepoRoot\.env")) {
    Write-Host "  -> Creating .env with auto-generated secure token..." -ForegroundColor Yellow
    $secret = [System.Guid]::NewGuid().ToString("N") + [System.Guid]::NewGuid().ToString("N")
    if (Test-Path "$RepoRoot\.env.example") {
        Copy-Item "$RepoRoot\.env.example" "$RepoRoot\.env"
    } else {
        New-Item -ItemType File -Path "$RepoRoot\.env" -Force | Out-Null
    }
    Add-Content -Path "$RepoRoot\.env" -Value "`nBETTER_AUTH_SECRET=$secret`nAGENT_WORKSPACE_AUTH_DISABLED=1`n"
}

# frontend/.env
if (-not (Test-Path "$RepoRoot\frontend\.env")) {
    Write-Host "  -> Creating frontend/.env..." -ForegroundColor Yellow
    Set-Content -Path "$RepoRoot\frontend\.env" -Value "NODE_ENV=development`n"
}

# config.yaml
if (-not (Test-Path "$RepoRoot\config.yaml")) {
    Write-Host "  -> Creating config.yaml from config.example.yaml..." -ForegroundColor Yellow
    Copy-Item "$RepoRoot\config.example.yaml" "$RepoRoot\config.yaml"
}

# extensions_config.json
if (-not (Test-Path "$RepoRoot\extensions_config.json")) {
    Write-Host "  -> Creating extensions_config.json..." -ForegroundColor Yellow
    if (Test-Path "$RepoRoot\extensions_config.example.json") {
        Copy-Item "$RepoRoot\extensions_config.example.json" "$RepoRoot\extensions_config.json"
    } else {
        Set-Content -Path "$RepoRoot\extensions_config.json" -Value "{}`n"
    }
}
Write-Host "  [OK] All configuration files prepared." -ForegroundColor Green

# 4. Install backend dependencies
Write-Host "`n[4/5] Installing Backend dependencies (uv sync)..." -ForegroundColor Yellow
Push-Location "$RepoRoot\backend"
try {
    # `uv` reports progress on stderr. Under $ErrorActionPreference = "Stop"
    # PowerShell treats stderr output from a native command as a terminating
    # error and aborts the script even when uv exits 0, so run these under
    # 'Continue' and judge them purely by $LASTEXITCODE.
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $uvPath sync --locked 2>&1 | ForEach-Object { Write-Host "  $_" }
    $syncExit = $LASTEXITCODE
    if ($syncExit -ne 0) {
        Write-Host "  -> Retrying uv sync without locked constraint..." -ForegroundColor Yellow
        & $uvPath sync 2>&1 | ForEach-Object { Write-Host "  $_" }
        $syncExit = $LASTEXITCODE
    }
    $ErrorActionPreference = $previousEap
    if ($syncExit -ne 0) {
        throw "uv sync failed (exit code $syncExit). Fix the backend dependency install before continuing."
    }
} finally {
    $ErrorActionPreference = 'Stop'
    Pop-Location
}
Write-Host "  [OK] Backend dependencies installed." -ForegroundColor Green

# 5. Install frontend dependencies
if (-not $SkipFrontend) {
    Write-Host "`n[5/5] Installing Frontend dependencies..." -ForegroundColor Yellow
    Push-Location "$RepoRoot\frontend"
    try {
        $previousEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $uvPath run --project "$RepoRoot\backend" python "$RepoRoot\scripts\pnpm.py" install 2>&1 | ForEach-Object { Write-Host "  $_" }
        $installExit = $LASTEXITCODE
        $ErrorActionPreference = $previousEap
        if ($installExit -ne 0) {
            throw "pnpm install failed (exit code $installExit)."
        }
    } catch {
        Write-Host "  -> Fallback to npm install..." -ForegroundColor Yellow
        $ErrorActionPreference = 'Continue'
        & npm install 2>&1 | ForEach-Object { Write-Host "  $_" }
        $npmExit = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($npmExit -ne 0) {
            Write-Host "[ERROR] Frontend dependency installation failed (exit code $npmExit)." -ForegroundColor Red
            Write-Host "Run 'cd frontend; pnpm install' manually and re-run this script.`n" -ForegroundColor Yellow
            Pop-Location
            exit 1
        }
    } finally {
        $ErrorActionPreference = 'Stop'
        Pop-Location
    }
    Write-Host "  [OK] Frontend dependencies installed." -ForegroundColor Green
}

Write-Host "`n========================================================" -ForegroundColor Green
Write-Host "           Installation Completed Successfully!          " -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
Write-Host "`nTo start Alpha and open the web browser, simply run:" -ForegroundColor Cyan
Write-Host "   .\start.ps1" -ForegroundColor White
Write-Host "or double-click start.bat`n" -ForegroundColor White
