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
#   powershell -NoProfile -STA -ExecutionPolicy Bypass -File recovery\tray_status.ps1
#   (registered for every logon by recovery\register_autostart.ps1 as Alpha_TrayStatus)

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
# How old alpha_health.json may be before it stops describing the present.
# Matches recovery/watchdog.ps1's LauncherHeartbeatMaxAge, which allows for
# start.ps1 legitimately pausing up to 300 s in backoff.
$HealthFileMaxAgeSeconds = 360

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

# ---- diagnostics (defined first: icon construction reports through it) ------
function Write-TrayDiag {
    param([string]$Message)
    try { Add-Content -Path "$LogDir\tray.log" -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message" } catch {}
}

# ---- icons (built once, cached - no repeated handle allocation) -------------
#
# The badge shows the real Alpha mark rather than a drawn letter, and every
# load, rejection and fallback is written to tray.log: "why is my icon a
# letter?" would otherwise be unanswerable from outside the process. The
# face-vs-mane ordering rule and the never-blank invariant are documented in
# recovery/AGENTS.md and pinned by backend/tests/test_tray_status_logo.py.

# Read one PNG payload straight out of a multi-resolution .ico.
#
# System.Drawing re-rasterises an .ico entry through Icon/ToBitmap, which can
# drop the alpha channel - the badge would then show a black square instead of
# a circle. The generated .ico stores PNG-compressed entries (the Vista+ form),
# so reading the container hands back the exact bytes generate-brand-assets.mjs
# wrote, and the entry is already at the size we want instead of being scaled.
function Get-IcoLayerPng {
    param([string]$IcoPath, [int]$Size)
    $bytes = [System.IO.File]::ReadAllBytes($IcoPath)
    if ($bytes.Length -lt 6) { throw "ico truncated ($($bytes.Length) bytes)" }
    $count = [System.BitConverter]::ToInt16($bytes, 4)   # ICONDIR: 0, type, count
    for ($i = 0; $i -lt $count; $i++) {
        $e = 6 + (16 * $i)                                # ICONDIRENTRY
        if (($e + 16) -gt $bytes.Length) { break }
        $w = [int]$bytes[$e]
        if ($w -eq 0) { $w = 256 }                        # 0 encodes 256
        if ($w -ne $Size) { continue }
        $len = [System.BitConverter]::ToInt32($bytes, $e + 8)
        $off = [System.BitConverter]::ToInt32($bytes, $e + 12)
        if ($len -le 0 -or ($off + $len) -gt $bytes.Length) { throw "ico entry #$i out of range" }
        $ms = New-Object System.IO.MemoryStream(,$bytes[$off..($off + $len - 1)])
        $img = $null
        try {
            $img = [System.Drawing.Image]::FromStream($ms)
            $bmp = New-Object System.Drawing.Bitmap($img)
        } finally {
            if ($img) { $img.Dispose() }
            $ms.Dispose()
        }
        return $bmp
    }
    throw "ico carries no ${Size}px layer"
}

function Get-AlphaLogo {
    # The badge clips the mark into a 22px circle, and generate-brand-assets.mjs
    # switches crop at FACE_MAX_SIZE (24): at or below it the FACE reads and the
    # MANE averages into an unreadable dark blob. So the face sources lead and
    # the mane ones are only a last resort.
    #
    # The poster itself (frontend/src/assets/images/alpha.png) is deliberately
    # NOT a candidate: it carries the ALPHA wordmark and the tagline, and no
    # mark may include the type. Only the generated crops are eligible.
    $ico = "$RepoRoot\electron\assets\alpha.ico"
    $candidates = @(
        @{ Label = "face 24 (alpha.ico)";     Ico  = $ico; IcoSize = 24 },
        @{ Label = "face 16 (favicon)";       Path = "$RepoRoot\frontend\public\favicon-16x16.png" },
        @{ Label = "mane 32 (favicon)";       Path = "$RepoRoot\frontend\public\favicon-32x32.png" },
        @{ Label = "mane 192 (pwa)";          Path = "$RepoRoot\frontend\public\icon-192.png" },
        @{ Label = "mane 512 (shell)";        Path = "$RepoRoot\electron\assets\alpha-mark.png" }
    )
    foreach ($c in $candidates) {
        if ($c.Ico) {
            if (-not (Test-Path -LiteralPath $c.Ico)) {
                Write-TrayDiag "logo candidate absent ($($c.Label)): $($c.Ico)"
                continue
            }
            try {
                $bmp = Get-IcoLayerPng -IcoPath $c.Ico -Size $c.IcoSize
                Write-TrayDiag "logo loaded ($($c.Label)): $($bmp.Width)x$($bmp.Height)"
                return $bmp
            } catch {
                Write-TrayDiag "logo load FAILED ($($c.Label)) -> $($_.Exception.GetType().FullName): $($_.Exception.Message)"
                continue
            }
        }
        if (-not (Test-Path -LiteralPath $c.Path)) {
            Write-TrayDiag "logo candidate absent ($($c.Label)): $($c.Path)"
            continue
        }
        try {
            # Clone out of FromFile and dispose the source at once: FromFile
            # holds an exclusive lock on the file for as long as it lives,
            # which would block asset regeneration and `next build` for the
            # whole life of the tray process.
            $src  = [System.Drawing.Image]::FromFile($c.Path)
            $copy = New-Object System.Drawing.Bitmap($src)
            $src.Dispose()
            Write-TrayDiag "logo loaded ($($c.Label)): $($copy.Width)x$($copy.Height)"
            return $copy
        } catch {
            # Reported rather than swallowed - this is exactly the "which file
            # failed, and why" an operator needs when the badge shows a letter.
            Write-TrayDiag "logo load FAILED ($($c.Label)) -> $($_.Exception.GetType().FullName): $($_.Exception.Message)"
        }
    }
    Write-TrayDiag "logo unavailable (tried $($candidates.Count) tracked assets) - using the drawn 'A' fallback"
    return $null
}

function New-StatusIcon {
    param([string]$Color, [string]$Glyph, [System.Drawing.Bitmap]$Logo = $null)
    $bmp = New-Object System.Drawing.Bitmap(32, 32)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.Clear([System.Drawing.Color]::Transparent)
    $fill = [System.Drawing.Color]::FromName($Color)
    $brush = New-Object System.Drawing.SolidBrush($fill)
    $g.FillEllipse($brush, 1, 1, 30, 30)

    # The mark is clipped to an inner circle so the state colour survives as a
    # ring around it: LimeGreen/Orange/Crimson/DimGray stays readable at tray
    # size instead of being covered by the logo. The source marks are opaque
    # squares, so without the clip their corners would square off the badge.
    $drewLogo = $false
    if ($Logo) {
        try {
            $clip = New-Object System.Drawing.Drawing2D.GraphicsPath
            $clip.AddEllipse(5, 5, 22, 22)
            $g.SetClip($clip)
            $g.DrawImage($Logo, (New-Object System.Drawing.Rectangle(5, 5, 22, 22)))
            $g.ResetClip()
            $clip.Dispose()
            $drewLogo = $true
        } catch {
            Write-TrayDiag "logo composite FAILED: $($_.Exception.GetType().FullName): $($_.Exception.Message) - using the glyph"
        }
    }

    # White hairline drawn last so it sits on top of both fills.
    $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::White, 2)
    $g.DrawEllipse($pen, 1, 1, 30, 30)
    if (-not $drewLogo -and $Glyph) {
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

$AlphaLogo = Get-AlphaLogo

$IconHealthy   = New-StatusIcon -Color "LimeGreen"    -Glyph "A" -Logo $AlphaLogo
$IconWorking   = New-StatusIcon -Color "Orange"       -Glyph "A" -Logo $AlphaLogo
# The failure badge keeps the "!" instead of the mark: on the one state where
# the colour alone is easy to miss, the glyph carries the signal.
$IconFailed    = New-StatusIcon -Color "Crimson"      -Glyph "!"
$IconStopped   = New-StatusIcon -Color "DimGray"      -Glyph "A" -Logo $AlphaLogo

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

    # Is the health file still trustworthy?
    #
    # The file is written by start.ps1, so a stack started any other way (make
    # dev, scripts/serve.sh, a leftover from an earlier run) has no writer, and the
    # file on disk describes a launcher that is long gone. The status branches
    # below are ordered "failed" -> "starting" -> ... -> "healthy", so a stale
    # "starting" was matched BEFORE the live probes were ever consulted: the
    # indicator sat on "booting services" indefinitely while both services
    # answered HTTP 200. That is exactly the claim this tray exists not to make.
    #
    # So the file must earn the right to describe the current state: it has to be
    # recent, and the PID it names has to still exist. 360 s matches
    # recovery/watchdog.ps1's LauncherHeartbeatMaxAge, which allows for the
    # launcher legitimately pausing in backoff.
    $healthUsable = $false
    $healthAge = -1
    if ($health) {
        try {
            $healthAge = [int](([DateTime]::UtcNow - [DateTime]::Parse($health.timestamp_utc, $null,
                [System.Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()).TotalSeconds)
        } catch {}
        $healthPidAlive = $false
        if ($health.pid) { $healthPidAlive = [bool](Get-Process -Id ([int]$health.pid) -ErrorAction SilentlyContinue) }
        $healthUsable = ($healthAge -ge 0 -and $healthAge -le $HealthFileMaxAgeSeconds -and $healthPidAlive)
    }

    $gw = Test-PortUp -Port $GatewayPort
    $fe = Test-PortUp -Port $FrontendPort
    $gwHttp = $false; $feHttp = $false
    if ($gw) { $gwHttp = Test-Http200 -Url "http://127.0.0.1:$GatewayPort/health/ready" }
    if ($fe) { $feHttp = Test-Http200 -Url "http://127.0.0.1:$FrontendPort/" }

    if (-not $health -and -not $gw -and -not $fe) {
        return @{ Key = "stopped"; ToolTip = "Alpha - Stopped"; Icon = $IconStopped }
    }

    # A file that is too old, or that names a PID which no longer exists, is
    # evidence about the past, not the present. Fall through to the live probes
    # rather than reporting what it once said.
    $status = ""
    if ($healthUsable) {
        try { $status = [string]$health.status } catch {}
    }

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
        # Both services answered, so Alpha is genuinely up. The age is reported
        # only when the health file is usable; otherwise the tooltip says the
        # state came from a live probe, which is the honest description.
        if ($healthUsable) {
            return @{ Key = "healthy"; ToolTip = "Alpha - Running (healthy, checked ${healthAge}s ago)"; Icon = $IconHealthy }
        }
        return @{ Key = "healthy"; ToolTip = "Alpha - Running (healthy, live check)"; Icon = $IconHealthy }
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
    # Start-Process joins the array WITHOUT quoting elements, so any argument
    # containing spaces (the repo path, on most machines: "C:\Users\John
    # Doe\...") must be quoted here or powershell.exe truncates it at the
    # first space and the script silently never runs.
    $quoted = @($ScriptArgs | ForEach-Object {
        if ($_ -match '\s' -and $_ -notmatch '^".*"$') { '"' + $_ + '"' } else { $_ }
    })
    Start-Process -FilePath "powershell.exe" -ArgumentList (@(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File'
    ) + $quoted) -WorkingDirectory $RepoRoot -WindowStyle Hidden | Out-Null
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

# Diagnostics: if the icon never appears, tray.log tells us where it stopped.
Write-TrayDiag "PID=$PID icons built (logo=$(if ($AlphaLogo) { 'mark' } else { 'glyph-fallback' })), notify-icon visible=$($ni.Visible), entering message loop"
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
