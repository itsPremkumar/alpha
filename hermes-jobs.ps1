# Control all Alpha Hermes cron jobs.
#   .\hermes-jobs.ps1 status    - list every job across all profiles
#   .\hermes-jobs.ps1 pause    - stop ALL autonomous work immediately
#   .\hermes-jobs.ps1 resume   - re-enable all jobs
#   .\hermes-jobs.ps1 doctor   - health check every job
#   .\hermes-jobs.ps1 runs <p> - show execution history for one profile

param(
    [Parameter(Position = 0)]
    [ValidateSet('status', 'pause', 'resume', 'doctor', 'runs')]
    [string]$Action = 'status',
    [string]$Profile = 'alpha-security'
)

$h = "$env:LOCALAPPDATA\hermes\hermes-agent\.hermes\bin\hermes.exe"

$profiles = @(
    'alpha-lead', 'alpha-runtime', 'alpha-memory', 'alpha-security',
    'alpha-api', 'alpha-qa', 'alpha-frontend', 'alpha-infra', 'alpha-critic'
)

switch ($Action) {
    'status' {
        foreach ($p in $profiles) {
            Write-Output "===== $p ====="
            & $h -p $p cron list 2>&1 | Select-Object -First 12
            Write-Output ""
        }
    }
    'pause' {
        foreach ($p in $profiles) {
            & $h -p $p cron pause '*' 2>&1 | Out-Null
        }
        Write-Output "ALL Alpha cron jobs PAUSED."
    }
    'resume' {
        foreach ($p in $profiles) {
            & $h -p $p cron resume '*' 2>&1 | Out-Null
        }
        Write-Output "ALL Alpha cron jobs RESUMED."
    }
    'doctor' {
        foreach ($p in $profiles) {
            Write-Output "===== $p ====="
            & $h -p $p cron doctor 2>&1 | Select-Object -First 15
            Write-Output ""
        }
    }
    'runs' {
        & $h -p $Profile cron runs 2>&1 | Select-Object -First 40
    }
}