<#
    Alpha - create the Start Menu and desktop shortcuts for an installed Alpha.

    Called by installer\bootstrap.ps1, and safe to run on its own afterwards:

        powershell -ExecutionPolicy Bypass -File installer\create-shortcuts.ps1

    Additive: it creates shortcut files and overwrites its own shortcut files.
    It never removes anything and never touches the install itself.
#>

[CmdletBinding()]
param(
    [string]$RepoPath,

    [string]$InstallRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not $RepoPath) {
    if (-not $InstallRoot) { $InstallRoot = Join-Path $env:LOCALAPPDATA 'Alpha' }
    $RepoPath = Join-Path $InstallRoot 'alpha'
}

$launcher = Join-Path $RepoPath 'start.ps1'
if (-not (Test-Path -LiteralPath $launcher)) {
    Write-Error "Alpha is not installed: no start.ps1 at $launcher. Run installer\bootstrap.ps1 first."
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$startMenuDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Alpha'
$desktop = [Environment]::GetFolderPath('Desktop')

# Start-Process quotes the -File argument for us, so the same quoting works for
# a path with spaces in it ("C:\Users\Jane Doe\Alpha\...").
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -NoBrowser' -f $launcher

function New-AlphaShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Description
    )
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = 'powershell.exe'
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = $RepoPath
    $shortcut.IconLocation = 'shell32.dll,13'
    $shortcut.Description = $Description
    $shortcut.Save()
    return $Path
}

$created = New-Object System.Collections.ArrayList

if (-not (Test-Path -LiteralPath $startMenuDir)) {
    New-Item -ItemType Directory -Path $startMenuDir -Force -ErrorAction Stop | Out-Null
}
[void]$created.Add((New-AlphaShortcut -Path (Join-Path $startMenuDir 'Alpha.lnk') -Description 'Start Alpha (local gateway + web UI)'))

# Also drop it in the common Programs folder so it shows up for every user, not
# only the one who installed. Best effort: a locked-down profile is not a reason
# to fail the install.
try {
    $commonPrograms = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs'
    if (Test-Path -LiteralPath $commonPrograms) {
        [void]$created.Add((New-AlphaShortcut -Path (Join-Path $commonPrograms 'Alpha.lnk') -Description 'Start Alpha (local gateway + web UI)'))
    }
} catch {
    Write-Warning "Could not write the all-users Start Menu shortcut: $($_.Exception.Message)"
}

if ($desktop -and (Test-Path -LiteralPath $desktop)) {
    [void]$created.Add((New-AlphaShortcut -Path (Join-Path $desktop 'Alpha.lnk') -Description 'Start Alpha (local gateway + web UI)'))
}

foreach ($path in $created) {
    Write-Host "shortcut: $path"
}
Write-Host ("{0} shortcut(s) created." -f $created.Count)
exit 0
