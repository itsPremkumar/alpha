$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root 'backend\.venv\Scripts\python.exe'
$env:PYTHONPATH = Join-Path $root 'backend'
Set-Location (Join-Path $root 'backend')
& $py 'scripts\check_tool_schemas.py' 2>&1 | Out-File -FilePath (Join-Path $root 'logs\tool_schema_check.txt') -Encoding utf8
exit $LASTEXITCODE
