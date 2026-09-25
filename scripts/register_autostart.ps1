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
$TaskNameUpdate = "Alpha_Update"
$AutostartDir   = "$RepoRoot\scripts\autostart"
$UpdateScript   = "$RepoRoot\scripts\auto_update.ps1"
$UpdatePolicy   = if ($env:ALPHA_UPDATE_POLICY_PATH) { $env:ALPHA_UPDATE_POLICY_PATH } else { "$RepoRoot\config\update-policy.json" }

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

function Get-UpdateEnabled {
    if (-not (Test-Path $UpdatePolicy)) { return $false }
    try {
        $policy = Get-Content $UpdatePolicy -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        return ($policy.enabled -is [bool] -and $policy.enabled)
    } catch {
        Write-Host "[WARN] update policy is unreadable; updater task will not be registered." -ForegroundColor Yellow
        return $false
    }
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

# ---- Task 3: Alpha_TrayStatus -- status icon in the notification area ------
$TrayScript  = "$RepoRoot\scripts\tray_status.ps1"
$TaskNameTray = "Alpha_TrayStatus"
if (-not (Test-Path $TrayScript)) {
    Write-Host "[WARN] tray_status.ps1 not found - skipping the tray indicator task." -ForegroundColor Yellow
} else {
    $existingTray = Get-ScheduledTask -TaskName $TaskNameTray -ErrorAction SilentlyContinue
    if ($existingTray -and -not $Force) {
        Write-Host "[OK] '$TaskNameTray' already registered (use -Force to re-register)." -ForegroundColor Green
    } else {
        if ($existingTray) {
            Unregister-ScheduledTask -TaskName $TaskNameTray -Confirm:$false -ErrorAction SilentlyContinue
        }
        # No extra flags: Windows PowerShell starts -File scripts in STA mode,
        # which WinForms (NotifyIcon) requires. Flags after -File are script
        # parameters, so -STA must NOT be passed here.
        $vbsTray = New-VbsLauncher -Name "Alpha_Tray_Status" `
            -PsFile $TrayScript -PsFlags "" -WorkDir $RepoRoot

        $triggerTray = New-ScheduledTaskTrigger -AtLogon -RandomDelay (New-TimeSpan -Seconds 10)
        $actionTray  = New-ScheduledTaskAction -Execute "wscript.exe" `
                           -Argument ('//B //Nologo "' + $vbsTray + '"')
        $settingsTray = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 2)
        $principalTray = New-ScheduledTaskPrincipal `
            -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

        Register-ScheduledTask `
            -TaskName   $TaskNameTray `
            -Action     $actionTray `
            -Trigger    $triggerTray `
            -Settings   $settingsTray `
            -Principal  $principalTray `
            -Description 'Alpha AI Agent: tray status icon showing whether Alpha is running' `
            -Force | Out-Null

        Write-Host "[OK] Registered '$TaskNameTray' (tray status icon 10 s after logon)." -ForegroundColor Green
    }
}

# ---- Task 4: Alpha_Update -- opt-in source update supervisor ---------------
# This task is created only when config/update-policy.json enables the feature.
# It calls the detached transaction path; it never pulls or unpacks code in the
# Task Scheduler process itself.
$existingUpdate = Get-ScheduledTask -TaskName $TaskNameUpdate -ErrorAction SilentlyContinue
if ((Get-UpdateEnabled) -and (Test-Path $UpdateScript)) {
    if ($existingUpdate -and -not $Force) {
        Write-Host "[OK] '$TaskNameUpdate' already registered (use -Force to re-register)." -ForegroundColor Green
    } else {
        if ($existingUpdate) {
            Unregister-ScheduledTask -TaskName $TaskNameUpdate -Confirm:$false -ErrorAction SilentlyContinue
        }
        $updateFlags = "-Policy `"$UpdatePolicy`" -Command check -Auto"
        $vbsUpdate = New-VbsLauncher -Name "Alpha_Update_Check" `
            -PsFile $UpdateScript -PsFlags $updateFlags -WorkDir $RepoRoot
        $triggerUpdate = New-ScheduledTaskTrigger `
            -Once `
            -At ((Get-Date).AddMinutes(2)) `
            -RepetitionInterval (New-TimeSpan -Minutes 15)
        $actionUpdate = New-ScheduledTaskAction -Execute "wscript.exe" `
            -Argument ('//B //Nologo "' + $vbsUpdate + '"')
        $settingsUpdate = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
            -RestartCount 2 `
            -RestartInterval (New-TimeSpan -Minutes 2)
        $principalUpdate = New-ScheduledTaskPrincipal `
            -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask `
            -TaskName $TaskNameUpdate `
            -Action $actionUpdate `
            -Trigger $triggerUpdate `
            -Settings $settingsUpdate `
            -Principal $principalUpdate `
            -Description 'Alpha AI Agent: guarded GitHub source update check/apply' `
            -Force | Out-Null
        Write-Host "[OK] Registered '$TaskNameUpdate' (every 15 minutes; policy-controlled)." -ForegroundColor Green
    }
} elseif ($existingUpdate) {
    Unregister-ScheduledTask -TaskName $TaskNameUpdate -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "[OK] Removed '$TaskNameUpdate' because the update policy is disabled." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================================"  -ForegroundColor Green
Write-Host " Autostart registration complete!"                         -ForegroundColor Green
Write-Host "========================================================"  -ForegroundColor Green
Write-Host " Alpha will now:"                                          -ForegroundColor Cyan
Write-Host "   - Start automatically ~45s after Windows login"        -ForegroundColor White
Write-Host "   - Be checked every 5 minutes and relaunched if down"   -ForegroundColor White
Write-Host "   - Survive crashes via multi-layer watchdog recovery"   -ForegroundColor White
Write-Host "   - Show a live status icon in the taskbar tray"         -ForegroundColor White
if (Get-UpdateEnabled) { Write-Host "   - Check/apply guarded GitHub source updates"  -ForegroundColor White }
Write-Host ""
Write-Host " To remove autostart:  .\scripts\unregister_autostart.ps1" -ForegroundColor Gray
Write-Host ' To verify tasks:      Get-ScheduledTask -TaskName "Alpha_*"' -ForegroundColor Gray
Write-Host ""
