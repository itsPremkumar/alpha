# Alpha Autostart Registration
# Registers Windows Task Scheduler tasks so Alpha starts automatically on logon
# and a watchdog monitors it every 5 minutes.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\register_autostart.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\register_autostart.ps1 -Force

[CmdletBinding()]
param(
    [switch]$Force   # Re-register even if tasks already exist
)

$ErrorActionPreference = "Stop"
$RepoRoot       = Split-Path $PSScriptRoot -Parent
$WatchdogScript = "$RepoRoot\scripts\watchdog.ps1"
$TaskNameBoot   = "Alpha_Autostart"
$TaskNameWatch  = "Alpha_Watchdog"
$AutostartDir   = "$RepoRoot\scripts\autostart"

Write-Host ""
Write-Host "========================================================"  -ForegroundColor Cyan
Write-Host "  Alpha Autostart Registration"                            -ForegroundColor Cyan
Write-Host "========================================================"  -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $WatchdogScript)) {
    Write-Host "[ERROR] watchdog.ps1 not found at: $WatchdogScript" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $AutostartDir)) {
    New-Item -ItemType Directory -Path $AutostartDir -Force | Out-Null
}

# ---- Helper: VBS launcher for hidden execution ------------------------------
# wscript.exe //B ensures no console window appears when Task Scheduler fires.
function New-VbsLauncher {
    param(
        [string]$Name,
        [string]$PsFile,
        [string]$PsFlags,
        [string]$WorkDir
    )
    $vbsPath = "$AutostartDir\${Name}.vbs"
    # The PowerShell invocation handed to WScript.Shell. The script path is
    # quoted because -File needs it, and that quote has to survive VBScript
    # parsing: inside a VBScript string literal EVERY embedded double-quote must
    # be doubled. Emitting `sh.Run "... -File "C:\path" ..."` makes wscript fail
    # to compile the file ("Expected end of statement", exit code 1), so the
    # scheduled task runs, does nothing and the stack never starts.
    $psLine   = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' `
                + $PsFile + '" ' + $PsFlags
    $runArg   = '"' + ($psLine -replace '"', '""') + '"'
    $workDirQ = '"' + ($WorkDir  -replace '"', '""') + '"'
    $nl = [Environment]::NewLine
    $content  = 'Option Explicit' + $nl
    $content += 'Dim sh' + $nl
    $content += 'Set sh = CreateObject("WScript.Shell")' + $nl
    $content += 'sh.CurrentDirectory = ' + $workDirQ + $nl
    $content += 'sh.Run ' + $runArg + ', 0, False' + $nl
    # wscript reads .vbs as ANSI: a UTF-8 BOM is seen as a stray character and
    # the script dies on line 1 with "Invalid character". Write BOM-free ANSI.
    $enc = [System.Text.Encoding]::Default
    try {
        $ansiCp = [System.Globalization.CultureInfo]::CurrentCulture.TextInfo.ANSICodePage
        if ($ansiCp -gt 0) { $enc = [System.Text.Encoding]::GetEncoding($ansiCp) }
    } catch {}
    [System.IO.File]::WriteAllText($vbsPath, $content, $enc)
    return $vbsPath
}

# ---- Task 1: Alpha_Autostart -- fires 45 s after each logon -----------------
$existingBoot = Get-ScheduledTask -TaskName $TaskNameBoot -ErrorAction SilentlyContinue
if ($existingBoot -and -not $Force) {
    Write-Host "[OK] '$TaskNameBoot' already registered (use -Force to re-register)." -ForegroundColor Green
} else {
    if ($existingBoot) {
        Unregister-ScheduledTask -TaskName $TaskNameBoot -Confirm:$false -ErrorAction SilentlyContinue
    }

    $vbsBoot = New-VbsLauncher -Name "Alpha_Autostart" `
        -PsFile $WatchdogScript -PsFlags "-StartIfDown" -WorkDir $RepoRoot

    $trigger  = New-ScheduledTaskTrigger -AtLogon -RandomDelay (New-TimeSpan -Seconds 45)
    $action   = New-ScheduledTaskAction -Execute "wscript.exe" `
                    -Argument ('//B //Nologo "' + $vbsBoot + '"')
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 2)
    $principal = New-ScheduledTaskPrincipal `
        -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

    Register-ScheduledTask `
        -TaskName   $TaskNameBoot `
        -Action     $action `
        -Trigger    $trigger `
        -Settings   $settings `
        -Principal  $principal `
        -Description 'Alpha AI Agent: starts watchdog 45s after logon to ensure services are running' `
        -Force | Out-Null

    Write-Host "[OK] Registered '$TaskNameBoot' (fires 45 s after logon)." -ForegroundColor Green
}

# ---- Task 2: Alpha_Watchdog -- every 5 minutes indefinitely -----------------
$existingWatch = Get-ScheduledTask -TaskName $TaskNameWatch -ErrorAction SilentlyContinue
if ($existingWatch -and -not $Force) {
    Write-Host "[OK] '$TaskNameWatch' already registered (use -Force to re-register)." -ForegroundColor Green
} else {
    if ($existingWatch) {
        Unregister-ScheduledTask -TaskName $TaskNameWatch -Confirm:$false -ErrorAction SilentlyContinue
    }

    $vbsWatch = New-VbsLauncher -Name "Alpha_Watchdog_Check" `
        -PsFile $WatchdogScript -PsFlags "-Once" -WorkDir $RepoRoot

    # Use a time-based trigger with repetition so it fires every 5 minutes
    # starting 2 minutes after logon (give Alpha_Autostart time to run first).
    # New-ScheduledTaskTrigger -Once -At <time> -RepetitionInterval is the
    # reliable API on Win10/11; setting .Repetition directly on a logon trigger
    # only works on some versions.
    $startTime    = (Get-Date).AddMinutes(2)
    $triggerWatch = New-ScheduledTaskTrigger `
        -Once `
        -At $startTime `
        -RepetitionInterval (New-TimeSpan -Minutes 5)

    $action   = New-ScheduledTaskAction -Execute "wscript.exe" `
                    -Argument ('//B //Nologo "' + $vbsWatch + '"')
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 4) `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal `
        -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

    Register-ScheduledTask `
        -TaskName   $TaskNameWatch `
        -Action     $action `
        -Trigger    $triggerWatch `
        -Settings   $settings `
        -Principal  $principal `
        -Description 'Alpha AI Agent: checks every 5 minutes that services are healthy; relaunches if not' `
        -Force | Out-Null

    Write-Host "[OK] Registered '$TaskNameWatch' (every 5 minutes)." -ForegroundColor Green
}

Write-Host ""
Write-Host "========================================================"  -ForegroundColor Green
Write-Host " Autostart registration complete!"                         -ForegroundColor Green
Write-Host "========================================================"  -ForegroundColor Green
Write-Host " Alpha will now:"                                          -ForegroundColor Cyan
Write-Host "   - Start automatically ~45s after Windows login"        -ForegroundColor White
Write-Host "   - Be checked every 5 minutes and relaunched if down"   -ForegroundColor White
Write-Host "   - Survive crashes via multi-layer watchdog recovery"   -ForegroundColor White
Write-Host ""
Write-Host " To remove autostart:  .\scripts\unregister_autostart.ps1" -ForegroundColor Gray
Write-Host ' To verify tasks:      Get-ScheduledTask -TaskName "Alpha_*"' -ForegroundColor Gray
Write-Host ""
