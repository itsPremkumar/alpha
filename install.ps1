# Agent Workspace - One-Click Installer for Windows (PowerShell)
# Usage: .\install.ps1

[CmdletBinding()]
param (
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
Set-Location $RepoRoot

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host "    Agent Workspace - Automated Setup & Installation    " -ForegroundColor Cyan
Write-Host "========================================================`n" -ForegroundColor Cyan

# 1. Check & locate uv
Write-Host "[1/5] Checking Python / uv package manager..." -ForegroundColor Yellow
$uvCmd = Get-Command "uv" -ErrorAction SilentlyContinue
if (-not $uvCmd) {
    # Check default install locations
    $uvCandidates = @(
        "$env:USERPROFILE\.cargo\bin\uv.exe",
        "$env:APPDATA\uv\uv.exe",
        "$env:LOCALAPPDATA\Programs\uv\uv.exe"
    )
    foreach ($candidate in $uvCandidates) {
        if (Test-Path $candidate) {
            $env:PATH = ((Split-Path $candidate) + ";" + $env:PATH)
            $uvCmd = Get-Command "uv" -ErrorAction SilentlyContinue
            break
        }
    }
}

if (-not $uvCmd) {
    Write-Host "  -> 'uv' not found. Installing Astral uv automatically..." -ForegroundColor Yellow
    try {
        powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
        $env:PATH = ("$env:USERPROFILE\.cargo\bin;" + $env:PATH)
        $uvCmd = Get-Command "uv" -ErrorAction SilentlyContinue
    } catch {
        Write-Error "Failed to auto-install uv. Please install it manually from https://astral.sh/uv and retry."
        exit 1
    }
}
$uvVersion = & uv --version
Write-Host "  [OK] $uvVersion" -ForegroundColor Green

# 2. Check Node.js
Write-Host "`n[2/5] Checking Node.js runtime..." -ForegroundColor Yellow
$nodeCmd = Get-Command "node" -ErrorAction SilentlyContinue
if (-not $nodeCmd) {
    Write-Error "Node.js (version 22+) is required. Please install it from https://nodejs.org/ and rerun this script."
    exit 1
}
$nodeVersion = & node -v
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
    & uv sync --locked
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  -> Retrying uv sync without locked constraint..." -ForegroundColor Yellow
        & uv sync
        if ($LASTEXITCODE -ne 0) {
            throw "uv sync failed (exit code $LASTEXITCODE). Fix the backend dependency install before continuing."
        }
    }
} finally {
    Pop-Location
}
Write-Host "  [OK] Backend dependencies installed." -ForegroundColor Green

# 5. Install frontend dependencies
if (-not $SkipFrontend) {
    Write-Host "`n[5/5] Installing Frontend dependencies..." -ForegroundColor Yellow
    Push-Location "$RepoRoot\frontend"
    try {
        & uv run --project "$RepoRoot\backend" python "$RepoRoot\scripts\pnpm.py" install
    } catch {
        Write-Host "  -> Fallback to npm install..." -ForegroundColor Yellow
        & npm install
    } finally {
        Pop-Location
    }
    Write-Host "  [OK] Frontend dependencies installed." -ForegroundColor Green
}

Write-Host "`n========================================================" -ForegroundColor Green
Write-Host "           Installation Completed Successfully!          " -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
Write-Host "`nTo start Agent Workspace and open the web browser, simply run:" -ForegroundColor Cyan
Write-Host "   .\start.ps1" -ForegroundColor White
Write-Host "or double-click start.bat`n" -ForegroundColor White
