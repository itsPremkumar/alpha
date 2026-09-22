# Alpha - Windows notification-area (system tray) status indicator
#
# Shows at a glance whether Alpha is running, directly in the taskbar tray:
#   GREEN  "Alpha - Running (healthy)"      both services answering HTTP
#   AMBER  "Alpha - Starting/Recovering"    boot or automatic recovery in progress
#   AMBER  "Alpha - Degraded"               a service is down but being healed
#   RED    "Alpha - Failed"                 launcher reported a failed startup
#   GREY   "Alpha - Stopped (maintenance)"  intentional stop via stop.ps1
#   GREY   "Alpha - Stopped"                nothing running
#
# Left-click  : open the Alpha UI (http://localhost:3000)
# Right-click : Open UI / Start / Stop / Restart / Exit indicator
#
# The indicator is deliberately INDEPENDENT of Alpha itself: it only reads the
# state files and probes the ports, so it keeps working (and truthfully shows
# "Stopped") while Alpha is down. It never starts or stops anything on its own.
#
# Usage:
#   powershell -NoProfile -STA -ExecutionPolicy Bypass -File scripts\tray_status.ps1
#   (registered for every logon by scripts\register_autostart.ps1 as Alpha_TrayStatus)

[CmdletBinding()]
param()

$ErrorActionPreference = "SilentlyContinue"
$RepoRoot        = Split-Path $PSScriptRoot -Parent
$LogDir          = "$RepoRoot\logs"
$HealthFile      = "$LogDir\alpha_health.json"
$MaintenanceFile = "$LogDir\alpha_maintenance.json"
$TrayPidFile     = "$LogDir\tray.pid"
$GatewayPort     = 8001
$FrontendPort    = 3000
$UiUrl           = "http://localhost:$FrontendPort"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

# ---- single instance --------------------------------------------------------
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$alreadyRunning = $false
if (Test-Path $TrayPidFile) {
    try {
        $prev = [int](Get-Content $TrayPidFile -Raw -ErrorAction Stop)
        if ($prev -gt 0 -and $prev -ne $PID -and (Get-Process -Id $prev -ErrorAction SilentlyContinue)) {
            $alreadyRunning = $true
        }
    } catch {}
}
if ($alreadyRunning) { exit 0 }
[System.IO.File]::WriteAllText($TrayPidFile, "$PID")

# ---- icons (built once, cached - no repeated handle allocation) -------------
function New-StatusIcon {
    param([string]$Color, [string]$Glyph)
    $bmp = New-Object System.Drawing.Bitmap(32, 32)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.Clear([System.Drawing.Color]::Transparent)
    $fill = [System.Drawing.Color]::FromName($Color)
    $brush = New-Object System.Drawing.SolidBrush($fill)
    $g.FillEllipse($brush, 1, 1, 30, 30)
    $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::White, 2)
    $g.DrawEllipse($pen, 1, 1, 30, 30)
    if ($Glyph) {
        $font = New-Object System.Drawing.Font("Segoe UI", 13, [System.Drawing.FontStyle]::Bold)
        $fmt = New-Object System.Drawing.StringFormat
        $fmt.Alignment = [System.Drawing.StringAlignment]::Center
        $fmt.LineAlignment = [System.Drawing.StringAlignment]::Center
        $g.DrawString($Glyph, $font, [System.Drawing.Brushes]::White,
            (New-Object System.Drawing.RectangleF(0, 0, 32, 32)), $fmt)
    }
    $g.Dispose()
    $hicon = $bmp.GetHicon()
    # FromHandle icon + bitmap are kept for the process lifetime (4 colors max).
    return @{
        Icon  = [System.Drawing.Icon]::FromHandle($hicon)
        Bitmap = $bmp
    }
}

$IconHealthy   = New-StatusIcon -Color "LimeGreen"    -Glyph "A"
$IconWorking   = New-StatusIcon -Color "Orange"       -Glyph "A"
$IconFailed    = New-StatusIcon -Color "Crimson"      -Glyph "!"
$IconStopped   = New-StatusIcon -Color "DimGray"      -Glyph "A"

# ---- status evaluation (read-only) -----------------------------------------
function Test-PortUp {
    param([int]$Port)
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1)
}
function Test-Http200 {
    param([string]$Url)
    try {
        return ((Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 4 -ErrorAction Stop).StatusCode -eq 200)
    } catch { return $false }
}

function Get-AlphaState {
    # Returns @{ Key; ToolTip; IconSet }
    if (Test-Path $MaintenanceFile) {
        return @{ Key = "maintenance"; ToolTip = "Alpha - Stopped (maintenance mode)"; Icon = $IconStopped }
    }
    $health = $null
    if (Test-Path $HealthFile) {
        try { $health = Get-Content $HealthFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop } catch {}
    }
    $gw = Test-PortUp -Port $GatewayPort
    $fe = Test-PortUp -Port $FrontendPort
    $gwHttp = $false; $feHttp = $false
    if ($gw) { $gwHttp = Test-Http200 -Url "http://127.0.0.1:$GatewayPort/health/ready" }
    if ($fe) { $feHttp = Test-Http200 -Url "http://127.0.0.1:$FrontendPort/" }

    if (-not $health -and -not $gw -and -not $fe) {
        return @{ Key = "stopped"; ToolTip = "Alpha - Stopped"; Icon = $IconStopped }
    }

    $status = ""
    try { $status = [string]$health.status } catch {}

    if ($status -eq "failed") {
        $detail = ""
        try { $detail = [string]$health.detail } catch {}
        return @{ Key = "failed"; ToolTip = "Alpha - Failed to start ($detail)"; Icon = $IconFailed }
    }
    if ($status -in @("starting", "recovering")) {
        $detail = ""
        try { $detail = [string]$health.detail } catch {}
        return @{ Key = "working"; ToolTip = "Alpha - $status ... $(if ($detail) { $detail } else { 'booting services' })"; Icon = $IconWorking }
    }
    if ($status -eq "degraded") {
        return @{ Key = "degraded"; ToolTip = "Alpha - Degraded (auto-recovery in progress)"; Icon = $IconWorking }
    }
    if ($gwHttp -and $feHttp -and ($status -in @("healthy", "running", ""))) {
        $age = -1
        try {
            $age = [int](([DateTime]::UtcNow - [DateTime]::Parse($health.timestamp_utc, $null,
                [System.Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()).TotalSeconds)
        } catch {}
        return @{ Key = "healthy"; ToolTip = "Alpha - Running (healthy, checked ${age}s ago)"; Icon = $IconHealthy }
    }
    if ($gw -or $fe) {
        return @{ Key = "partial"; ToolTip = "Alpha - Partially up (gateway=$(if ($gwHttp) { 'OK' } else { 'down' }), frontend=$(if ($feHttp) { 'OK' } else { 'down' })) - healing"; Icon = $IconWorking }
    }
    return @{ Key = "unknown"; ToolTip = "Alpha - Not running (state: $(if ($status) { $status } else { 'unknown' }))"; Icon = $IconStopped }
}

# ---- notify icon ------------------------------------------------------------
$ni = New-Object System.Windows.Forms.NotifyIcon
$ni.Icon = $IconStopped.Icon
$ni.Text = "Alpha - checking..."
$ni.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$mOpen    = New-Object System.Windows.Forms.ToolStripMenuItem("Open Alpha UI")
$mStart   = New-Object System.Windows.Forms.ToolStripMenuItem("Start Alpha")
$mStop    = New-Object System.Windows.Forms.ToolStripMenuItem("Stop Alpha (maintenance)")
$mRestart = New-Object System.Windows.Forms.ToolStripMenuItem("Restart Alpha")
$mSep     = New-Object System.Windows.Forms.ToolStripSeparator
$mExit    = New-Object System.Windows.Forms.ToolStripMenuItem("Exit indicator")
[void]$menu.Items.AddRange(@($mOpen, $mStart, $mStop, $mRestart, $mSep, $mExit))
$ni.ContextMenuStrip = $menu

function Invoke-AlphaScript {
    param([string[]]$ScriptArgs)
    Start-Process -FilePath "powershell.exe" -ArgumentList (@(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File'
    ) + $ScriptArgs) -WorkingDirectory $RepoRoot -WindowStyle Hidden | Out-Null
}

$mOpen.Add_Click({ Start-Process $UiUrl })
$mStart.Add_Click({ Invoke-AlphaScript -ScriptArgs @("$RepoRoot\start.ps1", '-NoBrowser', '-WatchdogMode') })
$mStop.Add_Click({ Invoke-AlphaScript -ScriptArgs @("$RepoRoot\stop.ps1") })
$mRestart.Add_Click({ Invoke-AlphaScript -ScriptArgs @("$RepoRoot\start.ps1", '-NoBrowser', '-WatchdogMode', '-Force') })
$mExit.Add_Click({ $ni.Visible = $false; $ni.Dispose(); [System.Windows.Forms.Application]::Exit() })

# MouseClick (not Click): Click passes plain EventArgs with no .Button property,
# which would make left-click silently do nothing.
$ni.Add_MouseClick({
    param($s, $e)
    if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left) { Start-Process $UiUrl }
})

$script:LastKey = ""
function Update-TrayStatus {
    try {
        $state = Get-AlphaState
        $ni.Icon = $state.Icon.Icon
        # ToolTip.Text is capped at 63 chars by WinForms - keep it inside.
        $tip = $state.ToolTip
        if ($tip.Length -gt 63) { $tip = $tip.Substring(0, 60) + "..." }
        $ni.Text = $tip
        if ($state.Key -ne $script:LastKey) {
            $script:LastKey = $state.Key
            try {
                Add-Content -Path "$LogDir\tray.log" `
                    -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] state=$($state.Key) ($($state.ToolTip))"
            } catch {}
        }
        # Menu availability follows reality: you cannot stop what is not running.
        $running = ($state.Key -notin @("stopped", "maintenance"))
        $mStop.Enabled = $running
        $mRestart.Enabled = $running
    } catch {}
}

$script:FirstTick = $true
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 10000    # 10 s: cheap reads, no visible churn
$timer.Add_Tick({
    Update-TrayStatus
    # One-time balloon: visible proof the indicator is alive. Windows 11 parks
    # brand-new tray icons behind the ^ chevron (or behind Settings toggles),
    # so a silent icon is easy to miss - the balloon tells the user it exists
    # and shows the live state.
    if ($script:FirstTick) {
        $script:FirstTick = $false
        try {
            $st = Get-AlphaState
            $ni.ShowBalloonTip(8000, "Alpha status indicator is active",
                "$($st.ToolTip) - left-click the icon to open Alpha.",
                [System.Windows.Forms.ToolTipIcon]::Info)
        } catch {}
    }
})
$timer.Start()
Update-TrayStatus

function Write-TrayDiag {
    param([string]$Message)
    try { Add-Content -Path "$LogDir\tray.log" -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message" } catch {}
}

# Diagnostics: if the icon never appears, tray.log tells us where it stopped.
Write-TrayDiag "PID=$PID icons built, notify-icon visible=$($ni.Visible), entering message loop"
try {
    [System.Windows.Forms.Application]::Run()
    Write-TrayDiag "message loop ended normally"
} catch {
    Write-TrayDiag "FATAL: $($_.Exception.GetType().FullName): $($_.Exception.Message)"
} finally {
    $timer.Stop()
    $ni.Visible = $false
    $ni.Dispose()
    try { if ((Get-Content $TrayPidFile -Raw) -eq "$PID") { Remove-Item $TrayPidFile -Force } } catch {}
}
