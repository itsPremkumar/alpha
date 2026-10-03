# Advanced analysis runner: runs every scanner and writes a report per tool.
# Usage:  .\scripts\analysis\run_all.ps1
# Requires: backend\.venv already synced (uv sync) and analysis tools installed.

$ErrorActionPreference = "Continue"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
$Reports = Join-Path $Root "analysis-reports"
New-Item -ItemType Directory -Force -Path $Reports | Out-Null

$Py = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "backend\.venv not found - run 'cd backend; uv sync' first." -ForegroundColor Red
    exit 1
}

function Run-Step($name, $cmd) {
    Write-Host ""
    Write-Host "=== $name ===" -ForegroundColor Cyan
    $out = & $cmd 2>&1
    $file = Join-Path $Reports ($name -replace ' ','_')
    $file = $file + ".txt"
    $out | Out-File -FilePath $file -Encoding utf8
    $out | Select-Object -First 30 | ForEach-Object { Write-Host $_ }
    Write-Host "  -> exit $LASTEXITCODE; full report: $file" -ForegroundColor Yellow
}

# 1. ruff (configured project rules)
Run-Step "ruff lint" { & $Py -m ruff check $Root --config $Root\ruff.toml }
Run-Step "ruff format check" { & $Py -m ruff format --check $Root --config $Root\ruff.toml }

# 2. bandit
Run-Step "bandit" { & $Py -m bandit -r $Root\backend\packages -c $Root\bandit.ini -ll }

# 3. semgrep (python registry rules; needs network once)
Run-Step "semgrep" { & $Py -m semgrep --config p/python --error --exclude '/backend/tests' --exclude '/.venv' $Root }

# 4. vulture (dead code)
Run-Step "vulture" { & $Py -m vulture $Root\backend\packages --min-confidence 70 }

# 5. mypy
Run-Step "mypy" { & $Py -m mypy $Root\backend\packages --config-file $Root\mypy.ini }

# 6. pip-audit (needs network)
Run-Step "pip-audit" { & $Py -m pip_audit --desc }

# 7. pytest (configless, like CI)
$env:ALPHA_CONFIG_PATH = Join-Path $Root ".does-not-exist.yaml"
Run-Step "pytest" { & $Py -m pytest $Root\backend\tests -q }

Write-Host ""
Write-Host "Done. Reports in: $Reports" -ForegroundColor Green
