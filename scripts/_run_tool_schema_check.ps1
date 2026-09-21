$py = 'C:\Users\PREM KUMAR\Videos\alpha\backend\.venv\Scripts\python.exe'
$env:PYTHONPATH = 'C:\Users\PREM KUMAR\Videos\alpha\backend'
Set-Location 'C:\Users\PREM KUMAR\Videos\alpha\backend'
& $py 'scripts\check_tool_schemas.py' 2>&1 | Out-File -FilePath 'C:\Users\PREM KUMAR\Videos\alpha\logs\tool_schema_check.txt' -Encoding utf8
exit $LASTEXITCODE
