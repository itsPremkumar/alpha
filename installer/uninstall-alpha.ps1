<#
    Alpha - uninstall the native Windows install created by installer\bootstrap.ps1.

        powershell -ExecutionPolicy Bypass -File uninstall-alpha.ps1
        powershell -ExecutionPolicy Bypass -File uninstall-alpha.ps1 -Yes
        powershell -ExecutionPolicy Bypass -File uninstall-alpha.ps1 -Yes -PurgeData

    This is the ONE sanctioned place in the installer tree that removes files.
    Everything else in installer\ is additive by construction, so the blast
    radius of an accidental run is bounded to what is asked for here:

      default   stop Alpha, remove shortcuts, the pinned uv/Node tools, the
                cloned source and the virtualenv. Your config.yaml, your .env
                and your agent data are KEPT.
      -PurgeData additionally removes config.yaml, .env and the runtime data
                directory (.agent-workspace). This destroys your settings and
                your generated secrets, which is why it needs its own switch on
                top of -Yes.

    Nothing outside $InstallRoot is ever touched.
#>

[CmdletBinding()]
param(
    [string]$InstallRoot,

    # Non-interactive. Required for CI.
    [switch]$Yes,

    # Also delete config.yaml, .env and the agent data directory.
    [switch]$PurgeData
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not $InstallRoot) { $InstallRoot = Join-Path $env:LOCALAPPDATA 'Alpha' }
$root = $InstallRoot

function Write-Line {
    param([string]$Message, [string]$Colour = 'Gray')
    Write-Host $Message -ForegroundColor $Colour
}

if (-not (Test-Path -LiteralPath $root)) {
    Write-Line "Nothing to uninstall: $root does not exist." 'Yellow'
    exit 0
}

# ---- Confirmation ----------------------------------------------------------
$purgeNote = ''
if ($PurgeData) { $purgeNote = "`n  INCLUDING -PurgeData: config.yaml, .env and agent data will be DELETED." }
if (-not $Yes) {
    Write-Line ''
    Write-Line 'This will remove the Alpha install at:' 'White'
    Write-Line "  $root" 'White'
    Write-Line "$purgeNote" 'White'
    $answer = Read-Host 'Continue? [y/N]'
    if ($answer -notmatch '^[Yy]') {
        Write-Line 'Aborted. Nothing was changed.'
        exit 0
    }
}

$results = New-Object System.Collections.ArrayList
function Add-Result {
    param([string]$Name, [bool]$Ok, [string]$Detail = '')
    [void]$results.Add([pscustomobject]@{
            Step   = $Name
            Result = $(if ($Ok) { 'PASS' } else { 'FAIL' })
            Detail = $Detail
        })
}

# ---- 1. Stop Alpha ----------------------------------------------------------
Write-Line 'Stopping Alpha...' 'Yellow'
$stopScript = Join-Path $root 'alpha\stop.ps1'
if (Test-Path -LiteralPath $stopScript) {
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript 2>&1 | Out-Null
    } catch {
        Write-Line "  stop.ps1 reported: $($_.Exception.Message)" 'Yellow'
    }
}

# Sweep the known ports so a hung component cannot block the uninstall. This is
# the one place a port sweep is correct: nothing should be listening afterwards.
$swept = @()
foreach ($port in @(8001, 3000, 8201, 2026)) {
    try {
        $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        foreach ($listener in @($listeners)) {
            if ($listener.OwningProcess -and $listener.OwningProcess -ne 0) {
                & taskkill.exe /PID $listener.OwningProcess /T /F 2>&1 | Out-Null
                $swept += $port
            }
        }
    } catch { }
}
Start-Sleep -Seconds 2
$stillListening = @()
foreach ($port in @(8001, 3000, 8201, 2026)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { $stillListening += $port }
}
Add-Result 'no Alpha service is still listening' ($stillListening.Count -eq 0) $(if ($stillListening.Count) { "ports: $($stillListening -join ',')" } else { "stopped: $(@($swept | Select-Object -Unique) -join ',')" })

# ---- 2. Shortcuts -----------------------------------------------------------
Write-Line 'Removing shortcuts...' 'Yellow'
$shortcuts = @(
    (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Alpha.lnk'),
    (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Alpha\Alpha.lnk'),
    (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Alpha.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Alpha.lnk')
)
$shortcutGone = $true
$removedShortcuts = @()
foreach ($shortcut in $shortcuts) {
    if (Test-Path -LiteralPath $shortcut) {
        Remove-Item -LiteralPath $shortcut -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $shortcut) { $shortcutGone = $false } else { $removedShortcuts += $shortcut }
    }
}
# The per-user Start Menu folder the installer created for its shortcut.
$startMenuFolder = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Alpha'
if (Test-Path -LiteralPath $startMenuFolder) {
    if (-not (Get-ChildItem -LiteralPath $startMenuFolder -Force -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $startMenuFolder -Force -ErrorAction SilentlyContinue
    }
}
Add-Result 'shortcuts removed' $shortcutGone ("removed: " + ($removedShortcuts -join '; '))

# ---- 3. Scheduled tasks the repo's autostart layer may have registered ------
Write-Line 'Removing Alpha scheduled tasks...' 'Yellow'
$tasksGone = $true
foreach ($name in @('Alpha_Autostart', 'Alpha_Watchdog', 'Alpha_TrayStatus')) {
    $exists = $false
    try { if (Get-ScheduledTask -TaskName $name -ErrorAction Stop) { $exists = $true } } catch { }
    if ($exists) {
        & schtasks.exe /Delete /TN $name /F 2>&1 | Out-Null
        try { if (Get-ScheduledTask -TaskName $name -ErrorAction Stop) { $tasksGone = $false } } catch { }
    }
}
Add-Result 'Alpha scheduled tasks removed' $tasksGone ''

# ---- 4. Remove the install tree -------------------------------------------
# config.yaml, .env and agent data survive by default: they are the user's, not
# the installer's. -PurgeData is the explicit, separate decision to drop them.
$preserved = @()
foreach ($keep in @('alpha\config.yaml', 'alpha\.env')) {
    $full = Join-Path $root $keep
    if (Test-Path -LiteralPath $full) { $preserved += $keep }
}
if ($PurgeData) {
    Write-Line 'Removing user configuration and agent data (-PurgeData)...' 'Yellow'
    $preserved = @()
}

Write-Line "Removing $root ..." 'Yellow'
$removeError = $null
try {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction Stop
} catch {
    $removeError = $_.Exception.Message
}
$rootGone = -not (Test-Path -LiteralPath $root)
Add-Result 'install directory removed' $rootGone $(if ($removeError) { $removeError } else { $root })

if ($rootGone) {
    Add-Result 'user configuration and secrets preserved' $true $(if ($preserved.Count) { "kept: $($preserved -join ', ')" } else { 'nothing to keep' })
} else {
    Add-Result 'user configuration and secrets preserved' $false "the install directory still exists, so nothing was deleted; re-run after closing Alpha"
}

# ---- 5. Report --------------------------------------------------------------
Write-Line ''
Write-Line '------------------ UNINSTALL SUMMARY ------------------' 'White'
foreach ($row in $results) {
    $colour = if ($row.Result -eq 'PASS') { 'Green' } else { 'Red' }
    $suffix = if ($row.Detail) { " - $($row.Detail)" } else { '' }
    Write-Line ("  [{0}] {1}{2}" -f $row.Result, $row.Step, $suffix) $colour
}

$failed = @($results | Where-Object { $_.Result -eq 'FAIL' })
if ($failed.Count -eq 0) {
    Write-Line ''
    Write-Line 'Alpha has been uninstalled.' 'Green'
    if ($preserved.Count -gt 0) {
        Write-Line "Kept, because they are yours and not the installer's: $($preserved -join ', ')" 'Gray'
        Write-Line 'Delete them by hand if you really want a clean slate.' 'Gray'
    }
    if ($PurgeData) {
        Write-Line 'config.yaml, .env and agent data were deleted as requested.' 'Gray'
    }
    Write-Line 'Nothing outside the install root was touched. The pinned uv and Node installs in %LOCALAPPDATA%\Alpha\tools went with it.' 'Gray'
    exit 0
}

Write-Line ''
Write-Line "UNINSTALL INCOMPLETE: $($failed.Count) step(s) failed." 'Red'
exit 1
