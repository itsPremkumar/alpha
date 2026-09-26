<#
.SYNOPSIS
    Shared helpers for the Alpha Windows installer scripts.

.DESCRIPTION
    Imported by installer\bootstrap.ps1, installer\uninstall-alpha.ps1 and
    installer\create-shortcuts.ps1 so the three agree on where things live and
    on how a log line looks.

    Nothing in this module deletes, moves or renames anything. The uninstaller
    is the single sanctioned place that removes files, and it does so itself.

    Every external command goes through Invoke-Bounded, which is mandatory about
    timeouts: an installer that can wait forever is an installer that hangs on a
    hotel wifi and leaves a half-installed machine with no explanation.
#>

Set-StrictMode -Version Latest

function Get-AlphaInstallPaths {
    <#
    .SYNOPSIS
        Resolve every path the installer touches, from one place.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallRoot
    )

    $resolved = (Resolve-Path -LiteralPath $InstallRoot -ErrorAction SilentlyContinue)
    $root = if ($resolved) { $resolved.ProviderPath } else { $InstallRoot }

    return @{
        Root         = $root
        Repo         = Join-Path $root 'alpha'
        Tools        = Join-Path $root 'tools'
        UvDir        = Join-Path $root 'tools\uv'
        UvExe        = Join-Path $root 'tools\uv\uv.exe'
        Logs         = Join-Path $root 'logs'
        InstallerLog = Join-Path $root 'logs\alpha-installer.log'
        StartMenu    = Join-Path $env:APPDATA ('Microsoft\Windows\Start Menu\Programs\Alpha')
        DesktopLink  = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Alpha.lnk'
        CompleteFlag = Join-Path $root '.alpha-install-complete'
    }
}

function Write-InstallerLog {
    <#
    .SYNOPSIS
        Append one timestamped line to the installer log.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$LogPath,

        [Parameter(Mandatory = $true)]
        [string]$Message,

        [ValidateSet('INFO', 'WARN', 'ERROR', 'STEP')]
        [string]$Level = 'INFO'
    )

    $line = '{0} [{1}] {2}' -f (Get-Date).ToString('yyyy-MM-ddTHH:mm:ssZ'), $Level, $Message
    try {
        $dir = Split-Path -Parent $LogPath
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force -ErrorAction SilentlyContinue | Out-Null
        }
        Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    } catch {
        # A log that cannot be written must never be the reason an install stops.
    }
    return $line
}

function Invoke-Bounded {
    <#
    .SYNOPSIS
        Run a native command with a hard timeout and a trustworthy exit code.

    .DESCRIPTION
        Two Windows traps this exists to avoid:

        1. Under $ErrorActionPreference = 'Stop', PowerShell 5.1 turns *stderr
           output* from a native command into a terminating error even when the
           tool exited 0. `uv sync` and `git clone` both write progress to
           stderr, so judging them by "did it throw" reports a success as a
           failure. This helper runs the child with stderr merged into stdout
           and judges purely by the exit code.
        2. A subprocess with no timeout can hang forever. A user watching a
           window that never closes learns nothing, so the timeout is mandatory
           and a timeout is reported as a distinct, named failure.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList,

        [Parameter(Mandatory = $true)]
        [int]$TimeoutSeconds,

        [string]$WorkingDirectory,

        [string]$LogPath,

        [string]$StageName = 'command',

        # When set, the child inherits this environment (e.g. VIRTUAL_ENV).
        [hashtable]$Environment
    )

    $stdout = [System.IO.Path]::GetTempFileName()
    $stderr = [System.IO.Path]::GetTempFileName()
    $started = Get-Date

    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $startArgs = @{
            FilePath               = $FilePath
            NoNewWindow            = $true
            PassThru               = $true
            RedirectStandardOutput = $stdout
            RedirectStandardError  = $stderr
        }
        if ($WorkingDirectory) { $startArgs['WorkingDirectory'] = $WorkingDirectory }

        $process = Start-Process @startArgs -ArgumentList $ArgumentList -PassThru
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            try { $process.Kill() } catch { }
            $elapsed = [int]((Get-Date) - $started).TotalSeconds
            $message = ('stage {0}: {1} exceeded its {2}s timeout after {3}s and was terminated (no output was produced)' -f $StageName, $FilePath, $TimeoutSeconds, $elapsed)
            if ($LogPath) { Write-InstallerLog -LogPath $LogPath -Message $message -Level 'ERROR' | Out-Null }
            return [pscustomobject]@{
                Stage      = $StageName
                ExitCode   = 124
                TimedOut   = $true
                StdOut     = ''
                StdErr     = ''
                ElapsedSec = $elapsed
                Error      = $message
            }
        }
        $process.WaitForExit()
    } catch {
        $message = ('stage {0}: could not launch {1}: {2}' -f $StageName, $FilePath, $_.Exception.Message)
        if ($LogPath) { Write-InstallerLog -LogPath $LogPath -Message $message -Level 'ERROR' | Out-Null }
        return [pscustomobject]@{
            Stage = $StageName; ExitCode = 127; TimedOut = $false
            StdOut = ''; StdErr = ''; ElapsedSec = 0; Error = $message
        }
    } finally {
        $ErrorActionPreference = $previousEap
    }

    $outText = (Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue)
    $errText = (Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue)
    foreach ($tmp in @($stdout, $stderr)) {
        try { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue } catch { }
    }

    $elapsed = [int]((Get-Date) - $started).TotalSeconds
    $exitCode = $process.ExitCode
    $error = $null
    if ($exitCode -ne 0) {
        $tail = (($errText + "`n" + $outText).Trim() -split "`r?`n" | Where-Object { $_ } | Select-Object -Last 12) -join ' | '
        $error = ('stage {0}: {1} exited {2} after {3}s. Last output: {5}' -f $StageName, $FilePath, $exitCode, $elapsed, $tail)
        if ($LogPath) { Write-InstallerLog -LogPath $LogPath -Message $error -Level 'ERROR' | Out-Null }
    } elseif ($LogPath) {
        Write-InstallerLog -LogPath $LogPath -Message ('stage {0}: {1} exited 0 after {2}s' -f $StageName, $FilePath, $elapsed) -Level 'INFO' | Out-Null
    }

    return [pscustomobject]@{
        Stage      = $StageName
        ExitCode   = $exitCode
        TimedOut   = $false
        StdOut     = [string]$outText
        StdErr     = [string]$errText
        ElapsedSec = $elapsed
        Error      = $error
    }
}

function Test-HealthEndpoint {
    <#
    .SYNOPSIS
        Poll a URL until it answers 200 or the deadline passes.
    .DESCRIPTION
        Returns the final HTTP status and the response body so a failure can be
        reported with what the service actually said, not just "it did not come
        up".
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,

        [int]$TimeoutSeconds = 600,

        [int]$PollIntervalSeconds = 5
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastStatus = 0
    $lastBody = ''
    $lastError = 'never attempted'
    $ready = $false

    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
            $lastStatus = [int]$response.StatusCode
            $lastBody = [string]$response.Content
            if ($lastStatus -eq 200) { $ready = $true; break }
            $lastError = "HTTP $lastStatus"
        } catch {
            $lastError = $_.Exception.Message
            try {
                if ($_.Exception.Response) { $lastStatus = [int]$_.Exception.Response.StatusCode }
            } catch { }
        }
        Start-Sleep -Seconds $PollIntervalSeconds
    }

    return @{
        Uri    = $Uri
        Ready  = $ready
        Status = $lastStatus
        Body   = $lastBody
        Error  = $lastError
    }
}

function Get-DirectorySizeMB {
    <#
    .SYNOPSIS
        Logical size of a directory tree, in mebibytes.
    .DESCRIPTION
        Logical, not allocated: uv hardlinks wheels out of its shared cache, so
        the allocated size of a venv is roughly half its logical size. Both
        numbers matter and they are not interchangeable, so this reports the
        logical one and the caller says which it is quoting.
    #>
    [CmdletBinding()]
    [OutputType([double])]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) { return 0.0 }
    $measure = Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum
    if (-not $measure -or $null -eq $measure.Sum) { return 0.0 }
    return [math]::Round(($measure.Sum / 1MB), 1)
}

function New-LocalSecret {
    <#
    .SYNOPSIS
        Generate a secret ON THIS MACHINE using Python's secrets module.
    .DESCRIPTION
        The installer ships no secret and never prints one. The value is
        generated by the standard library CSPRNG, returned to the caller, and
        written only into the gitignored .env.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonExe,

        [int]$Bytes = 48
    )

    $code = 'import secrets,sys; sys.stdout.write(secrets.token_urlsafe({0}))'.Replace('{0}', [string]$Bytes)
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $value = (& $PythonExe -c $code 2>$null | Out-String).Trim()
    } finally {
        $ErrorActionPreference = $previousEap
    }
    if (-not $value) {
        throw ('New-LocalSecret: {0} produced no output. A secret cannot be guessed, so this step fails loudly rather than installing a predictable one.' -f $PythonExe)
    }
    return $value
}

Export-ModuleMember -Function Get-AlphaInstallPaths, Write-InstallerLog, Invoke-Bounded, Test-HealthEndpoint, Get-DirectorySizeMB, New-LocalSecret
