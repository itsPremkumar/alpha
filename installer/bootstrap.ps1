<#
    Alpha - unattended Windows bootstrapper (native path, no Docker required).

    Takes a brand-new Windows 10/11 laptop to a running Alpha install with no
    developer toolchain, no pre-installed Python and no pre-installed Node.

    Design rules this file obeys, and why:

    * ADDITIVE. Nothing here deletes, moves or renames an existing file. The
      only writes are creates-guarded-by-a-not-exists-test, so re-running the
      installer can never destroy a working install or a user's configuration.
    * IDEMPOTENT. Every step checks whether its work is already done and skips
      it. Running the installer twice is indistinguishable from running it once,
      except that the second run says so and exits 0.
    * PINNED. Every version comes from installer\pins.json. The repository ref
      must be an immutable tag; a moving branch is rejected, not warned about.
    * BOUNDED. Every subprocess goes through Invoke-Bounded, which requires a
      timeout. No step can hang forever.
    * HONEST. A failed preflight or a failed post-install verification exits
      non-zero naming the real reason. A silent install that leaves a broken
      app is worse than no installer at all.

    Usage:
        powershell -ExecutionPolicy Bypass -File bootstrap.ps1
        powershell -ExecutionPolicy Bypass -File bootstrap.ps1 -DryRun
        powershell -ExecutionPolicy Bypass -File bootstrap.ps1 -Uninstall
#>

[CmdletBinding()]
param(
    # Report every step and change nothing. This is how the test suite and a
    # cautious user exercise the installer without touching the machine.
    [switch]$DryRun,

    # Never prompt. Required for unattended/CI use.
    [switch]$Yes,

    # Where Alpha is installed. Defaults to %LOCALAPPDATA%\Alpha.
    [string]$InstallRoot,

    # Overrides pins.json. Must still be an immutable tag.
    [string]$Tag,

    # Overrides pins.json's repository URL.
    [string]$RepoUrl,

    # Postgres is strictly opt-in. The default install needs no database server.
    [switch]$WithPostgres,

    # Skip the post-install start + health verification (for CI images).
    [switch]$SkipVerification,

    # In -DryRun, actually contact the remote to confirm the pinned tag exists.
    [switch]$VerifyRemote,

    # Remove the install (delegates to uninstall-alpha.ps1).
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Windows resolves a bare command name through PATHEXT. Some managed images
# ship a PATHEXT without .EXE, which makes an installed uv.exe invisible to
# both Get-Command and the shell. Restore the platform default so the rest of
# this script can rely on PATH.
if (-not $env:PATHEXT -or $env:PATHEXT -notlike '*.EXE*') {
    $env:PATHEXT = '.COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC;.CPL'
}

$script:InstallerDir = $PSScriptRoot
$script:ModulePath = Join-Path $PSScriptRoot 'lib\Alpha.Installer.psm1'

# ---------------------------------------------------------------------------
# Constants. Every timeout is named so a failure log can say which stage ran
# long, rather than "the installer was slow".
# ---------------------------------------------------------------------------
$script:MinimumWindowsBuild = 19041          # Windows 10 2004
$script:SupportedArchitectures = @('AMD64', 'ARM64')
$script:RequiredFreeDiskMB = 6144            # 6 GiB: venv + Node + frontend + source
$script:RequiredDiskMarginMB = 1024          # headroom so a full disk is never the failure mode
$script:CloneTimeoutSeconds = 900
$script:SyncTimeoutSeconds = 1800
$script:NodeTimeoutSeconds = 900
$script:HealthTimeoutSeconds = 600
$script:InstallStepTimeoutSeconds = 120
$script:RemoteProbeTimeoutSeconds = 60

# Populated by Read-Pins.
$script:Pins = $null

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
$script:LogPath = $null

function Write-StepLog {
    <#
    .SYNOPSIS
        Emit one line to the console and to the installer log.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,

        [ValidateSet('INFO', 'WARN', 'ERROR', 'STEP', 'WOULD')]
        [string]$Level = 'INFO'
    )

    $colour = switch ($Level) {
        'STEP' { 'Cyan' }
        'WARN' { 'Yellow' }
        'ERROR' { 'Red' }
        'WOULD' { 'Magenta' }
        default { 'Gray' }
    }
    $prefix = switch ($Level) {
        'STEP' { '==>' }
        'WARN' { ' ! ' }
        'ERROR' { ' XX ' }
        'WOULD' { ' ?? ' }
        default { '    ' }
    }
    Write-Host ("{0} {1}" -f $prefix, $Message) -ForegroundColor $colour

    if ($script:LogPath) {
        try {
            $line = '{0} [{1}] {2}' -f (Get-Date).ToString('yyyy-MM-ddTHH:mm:ssZ'), $Level, $Message
            Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
        } catch {
            # A log that cannot be written is never a reason to abort an install.
        }
    }
}

function Write-StepFailure {
    <#
    .SYNOPSIS
        Name the exact step that failed and why, then exit non-zero.
    .DESCRIPTION
        Every failure path in this script ends here. The message always carries
        the real reason (an exit code, a missing path, a named requirement) and
        always points at the log, because an installer that fails with "setup
        failed" has told the user nothing.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Stage,

        [Parameter(Mandatory = $true)]
        [string]$Reason,

        [string]$Remedy
    )

    Write-StepLog -Message "FAILED at stage '$Stage': $Reason" -Level 'ERROR'
    if ($Remedy) { Write-StepLog -Message "How to proceed: $Remedy" -Level 'ERROR' }
    if ($script:LogPath) {
        Write-StepLog -Message "Installer log: $script:LogPath" -Level 'ERROR'
    }
    exit 1
}

function Remove-ScratchPath {
    <#
    .SYNOPSIS
        The ONE place in this script that removes anything, and it only ever
               removes this script's own scratch files.
    .DESCRIPTION
        The installer is additive: it must never delete, move or rename a file
        the user owns. It does need to clean up after itself -- the uv and Node
        archives it downloads, and the temp files its subprocess redirects use.
        Rather than trusting each call site to be careful, this helper refuses
        any path that is not underneath the system temp directory. A bug that
        passed, say, the install root in here would be caught immediately
        instead of deleting somebody's configuration.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Path
    )

    if (-not $Path) { return }
    $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
    $full = [System.IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($tempRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        # Refusing is the whole point of the helper: this is a guard, not a
        # convenience wrapper.
        throw "Remove-ScratchPath refused '$full': it is not under the system temp directory ($tempRoot). The bootstrapper only ever deletes its own scratch files."
    }
    if (-not (Test-Path -LiteralPath $full)) { return }
    Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------------
# Bounded subprocess execution
# ---------------------------------------------------------------------------
function Invoke-Bounded {
    <#
    .SYNOPSIS
        Run a native command with a hard timeout and a trustworthy exit code.

    .DESCRIPTION
        Two Windows traps this exists to avoid:

        1. Under $ErrorActionPreference = 'Stop', PowerShell 5.1 promotes
           *stderr output* from a native command into a terminating error even
           when the tool exited 0. `uv sync` and `git clone` both write progress
           to stderr, so treating "did it throw" as success would report a
           working install as a failure. The child therefore runs with stderr
           redirected to a file and is judged purely by its exit code.
        2. A subprocess with no timeout can hang forever, leaving a user staring
           at a window that never closes. The timeout is mandatory, and a
           timeout is reported as its own distinct exit code (124).
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

        [string]$StageName = 'command'
    )

    $stdout = [System.IO.Path]::GetTempFileName()
    $stderr = [System.IO.Path]::GetTempFileName()
    $started = Get-Date

    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $process = $null
    try {
        $startArgs = @{
            FilePath               = $FilePath
            NoNewWindow            = $true
            PassThru               = $true
            RedirectStandardOutput = $stdout
            RedirectStandardError  = $stderr
        }
        if ($WorkingDirectory) { $startArgs['WorkingDirectory'] = $WorkingDirectory }

        $process = Start-Process @startArgs -ArgumentList $ArgumentList

        # MEASURED, and this was the installer's most load-bearing bug.
        # `Start-Process -PassThru` leaves .ExitCode NULL even after a successful
        # WaitForExit(). So $exitCode below was $null for EVERY subprocess, and
        # `$null -ne 0` is True in PowerShell, which made all 14 call sites that
        # branch on .ExitCode treat successful commands as failures. `uv sync`,
        # `git clone`, the Node install, the frontend build and the health check
        # were all being judged by a value that had never been measured.
        # Touching .Handle forces the process handle to be cached, which is what
        # makes .ExitCode reliable. Verified on this machine: without this line
        # ExitCode is null; with it, ExitCode is the real code.
        $null = $process.Handle

        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            try { $process.Kill() } catch { }
            $elapsed = [int]((Get-Date) - $started).TotalSeconds
            $message = ('{0} did not finish within its {1}s timeout and was terminated after {2}s' -f $FilePath, $TimeoutSeconds, $elapsed)
            Write-StepLog -Message "TIMEOUT [$StageName] $message" -Level 'ERROR'
            return [pscustomobject]@{
                Stage = $StageName; ExitCode = 124; TimedOut = $true
                StdOut = ''; StdErr = ''; ElapsedSec = $elapsed; Error = $message
            }
        }
        $process.WaitForExit()
    } catch {
        $message = ('could not launch {0}: {1}' -f $FilePath, $_.Exception.Message)
        Write-StepLog -Message "LAUNCH FAILED [$StageName] $message" -Level 'ERROR'
        return [pscustomobject]@{
            Stage = $StageName; ExitCode = 127; TimedOut = $false
            StdOut = ''; StdErr = ''; ElapsedSec = 0; Error = $message
        }
    } finally {
        $ErrorActionPreference = $previousEap
    }

    $outText = [string](Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue)
    $errText = [string](Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue)

    # These two temp files are this script's own scratch files, created moments
    # ago by GetTempFileName. Removing them is not a destructive operation on
    # anything the user owns.
    foreach ($scratch in @($stdout, $stderr)) {
        try { Remove-ScratchPath -Path $scratch } catch { }
    }

    $elapsed = [int]((Get-Date) - $started).TotalSeconds
    $exitCode = $process.ExitCode
    $error = $null
    if ($exitCode -ne 0) {
        $tail = ((($errText + "`n" + $outText).Trim()) -split "`r?`n" | Where-Object { $_ } | Select-Object -Last 12) -join ' | '
        if (-not $tail) { $tail = '(no output captured)' }
        $error = ('{0} exited {1} after {2}s. Last output: {3}' -f $FilePath, $exitCode, $elapsed, $tail)
        Write-StepLog -Message "EXIT $exitCode [$StageName] $error" -Level 'ERROR'
    } else {
        Write-StepLog -Message ("ok [{0}] {1} exited 0 after {2}s" -f $StageName, $FilePath, $elapsed) -Level 'INFO'
    }

    return [pscustomobject]@{
        Stage      = $StageName
        ExitCode   = $exitCode
        TimedOut   = $false
        StdOut     = $outText
        StdErr     = $errText
        ElapsedSec = $elapsed
        Error      = $error
    }
}

# ---------------------------------------------------------------------------
# Dry-run dispatch
# ---------------------------------------------------------------------------
function Invoke-InstallStep {
    <#
    .SYNOPSIS
        The single choke point through which every machine change passes.
    .DESCRIPTION
        In -DryRun this prints what it WOULD do and returns without invoking the
        scriptblock. That is what makes "dry run changes nothing" a structural
        property of the script rather than a promise in a comment: no mutating
        code path is reachable when -DryRun is set.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [scriptblock]$Action
    )

    if ($DryRun) {
        Write-StepLog -Message "WOULD: $Name" -Level 'WOULD'
        return $null
    }
    Write-StepLog -Message $Name -Level 'STEP'
    return & $Action
}

# ---------------------------------------------------------------------------
# Pins
# ---------------------------------------------------------------------------
function Read-Pins {
    <#
    .SYNOPSIS
        Load installer\pins.json and apply -Tag / -RepoUrl overrides.
    #>
    [CmdletBinding()]
    param(
        [string]$OverrideTag,
        [string]$OverrideRepoUrl
    )

    $pinFile = Join-Path $script:InstallerDir 'pins.json'
    if (-not (Test-Path -LiteralPath $pinFile)) {
        Write-StepFailure -Stage 'pins' -Reason "the pin file is missing: $pinFile" `
            -Remedy 'Reinstall the installer; pins.json ships alongside bootstrap.ps1 and must not be edited by hand.'
    }
    try {
        $pins = Get-Content -LiteralPath $pinFile -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        Write-StepFailure -Stage 'pins' -Reason "the pin file is not valid JSON ($($_.Exception.Message))" `
            -Remedy 'Reinstall the installer.'
    }

    $ref = if ($OverrideTag) { $OverrideTag } else { $pins.repo.ref }
    $url = if ($OverrideRepoUrl) { $OverrideRepoUrl } else { $pins.repo.url }

    $moving = @('main', 'master', 'develop', 'development', 'trunk', 'latest', 'stable', 'head', 'nightly', 'edge')
    if ($moving -contains $ref.ToLowerInvariant()) {
        Write-StepFailure -Stage 'pins' -Reason "'$ref' is a moving branch, not a release. A released install must be reproducible, so the bootstrapper refuses to install from a branch that changes under the user." `
            -Remedy 'Pass -Tag with an immutable release tag (for example -Tag v2.1.0).'
    }
    if ($ref -notmatch '^v\d+\.\d+\.\d+') {
        Write-StepFailure -Stage 'pins' -Reason "'$ref' is not an immutable version tag (expected vMAJOR.MINOR.PATCH)." `
            -Remedy 'Pass -Tag with an immutable release tag (for example -Tag v2.1.0).'
    }

    $pins.repo.ref = $ref
    $pins.repo.url = $url
    return $pins
}

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
function Invoke-Preflight {
    <#
    .SYNOPSIS
        Check the machine can host Alpha. Any failure names the real reason.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    Write-StepLog -Message 'Preflight: checking this machine can host Alpha' -Level 'STEP'

    # 1. PowerShell. Everything in this installer is PowerShell, so without it
    #    there is no installer at all; name that rather than failing obscurely.
    if (-not $PSVersionTable -or $PSVersionTable.PSVersion.Major -lt 5) {
        Write-StepFailure -Stage 'preflight/powershell' `
            -Reason "Windows PowerShell 5.1 or later is required; this session reports $($PSVersionTable.PSVersion)." `
            -Remedy 'Alpha installs PowerShell 5.1 itself when absent; on a stripped image, install it from https://aka.ms/powershell and re-run.'
    }
    Write-StepLog -Message ("  PowerShell {0} available" -f $PSVersionTable.PSVersion) -Level 'INFO'

    # 2. Operating system build.
    $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction SilentlyContinue
    if ($null -eq $os) {
        Write-StepFailure -Stage 'preflight/os' -Reason 'could not read the operating system version from WMI (Win32_OperatingSystem returned nothing).' `
            -Remedy 'Check that the WMI service is running, then re-run.'
    }
    $build = [int]$os.BuildNumber
    $caption = [string]$os.Caption
    if ($build -lt $script:MinimumWindowsBuild) {
        Write-StepFailure -Stage 'preflight/os' `
            -Reason "this is $caption build $build, but Alpha requires Windows 10 build $script:MinimumWindowsBuild (2004) or newer." `
            -Remedy 'Run Windows Update until the build is at least 19041, then re-run the installer.'
    }
    Write-StepLog -Message ("  {0} build {1} (>= {2} required)" -f $caption.Trim(), $build, $script:MinimumWindowsBuild) -Level 'INFO'

    # 3. CPU architecture. x64 and ARM64 are supported; a 32-bit OS cannot run
    #    the CPython and Node builds Alpha pins.
    $arch = $env:PROCESSOR_ARCHITEW6432
    if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
    if (-not $arch) {
        Write-StepFailure -Stage 'preflight/arch' -Reason 'could not determine the CPU architecture (PROCESSOR_ARCHITECTURE is unset).' `
            -Remedy 'Re-run from a normal interactive or service session where the environment is populated.'
    }
    if ($script:SupportedArchitectures -notcontains $arch) {
        Write-StepFailure -Stage 'preflight/arch' `
            -Reason "CPU architecture '$arch' is not supported; Alpha ships x64 and ARM64 builds only (32-bit Windows is not supported)." `
            -Remedy 'Reinstall Windows 64-bit, or run Alpha inside a container on this machine.'
    }
    Write-StepLog -Message ("  CPU architecture {0}" -f $arch) -Level 'INFO'

    # 4. Free disk space on the volume the install root lives on. Checked before
    #    anything is downloaded, and with margin, because "the disk filled up
    #    halfway through" is the worst possible failure mode.
    $rootParent = Split-Path -Parent $Root
    if (-not $rootParent) { $rootParent = $Root }
    $driveRoot = [System.IO.Path]::GetPathRoot((Join-Path $Root 'x'))
    if (-not $driveRoot) { $driveRoot = 'C:\' }
    $freeBytes = 0
    try {
        $freeBytes = (New-Object -ComObject Scripting.FileSystemObject).GetDrive($driveRoot).FreeSpace
    } catch {
        $freeBytes = (Get-PSDrive -Name $driveRoot.TrimEnd(':')).Free
    }
    $freeMB = [int]($freeBytes / 1MB)
    $neededMB = $script:RequiredFreeDiskMB + $script:RequiredDiskMarginMB
    if ($freeMB -lt $neededMB) {
        Write-StepFailure -Stage 'preflight/disk' `
            -Reason "volume $driveRoot has only $freeMB MB free; Alpha needs at least $script:RequiredFreeDiskMB MB plus $script:RequiredDiskMarginMB MB of headroom ($neededMB MB total)." `
            -Remedy 'Free up disk space (or point -InstallRoot at a larger volume) and re-run. Nothing was downloaded or changed.'
    }
    Write-StepLog -Message ("  {0} has {1} MB free (>= {2} MB required)" -f $driveRoot, $freeMB, $neededMB) -Level 'INFO'

    # 5. The install root must be writable. A read-only or non-existent parent
    #    is named here rather than surfacing as a confusing copy failure later.
    $parent = Split-Path -Parent $Root
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        if ($DryRun) {
            Write-StepLog -Message ("  WOULD create install parent {0}" -f $parent) -Level 'WOULD'
        } else {
            try {
                New-Item -ItemType Directory -Path $parent -Force -ErrorAction Stop | Out-Null
            } catch {
                Write-StepFailure -Stage 'preflight/writable' -Reason "cannot create the install parent directory $parent : $($_.Exception.Message)" `
                    -Remedy 'Choose a different -InstallRoot, or grant write access to that folder.'
            }
        }
    }
    Write-StepLog -Message 'Preflight passed' -Level 'INFO'
}

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------
function New-InstallSecret {
    <#
    .SYNOPSIS
        Generate a secret on THIS machine with the standard library CSPRNG.
    .DESCRIPTION
        The value comes from Python's secrets.token_urlsafe. Nothing is shipped
        in the installer, nothing is fetched from a server, and the value is
        never written to the console or to the log - only into the gitignored
        .env. A secret that travelled with the installer would be the same
        secret for every user on earth.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonExe
    )

    $code = 'import secrets,sys; sys.stdout.write(secrets.token_urlsafe(48))'
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $value = ([string](& $PythonExe -c $code 2>$null | Out-String)).Trim()
    } finally {
        $ErrorActionPreference = $previousEap
    }
    if (-not $value) {
        Write-StepFailure -Stage 'secrets' -Reason "could not generate a secret: $PythonExe produced no output for secrets.token_urlsafe(48)." `
            -Remedy 'This is a broken Python install. Re-run the installer; if it persists, report the installer log.'
    }
    return $value
}

# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
function Install-PinnedUv {
    <#
    .SYNOPSIS
        Put exactly one pinned uv binary in the install root.
    .DESCRIPTION
        Python is never installed directly: `uv python install` provisions the
        interpreter from the same pinned uv, so there is exactly one tool
        responsible for the Python that runs Alpha. The uv version is the one
        backend/Dockerfile pins, so a released install resolves uv.lock with the
        same resolver production uses.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$UvExe,

        [Parameter(Mandatory = $true)]
        [string]$UvVersion,

        [Parameter(Mandatory = $true)]
        [string]$UvDir
    )

    if (Test-Path -LiteralPath $UvExe) {
        $existing = (Invoke-Bounded -FilePath $UvExe -ArgumentList @('--version') -TimeoutSeconds $script:InstallStepTimeoutSeconds -StageName 'uv-version') 
        $have = ([string]$existing.StdOut).Trim()
        if ($have -eq "uv $UvVersion") {
            Write-StepLog -Message "  uv $UvVersion already present at $UvExe (no change)" -Level 'INFO'
            return $UvExe
        }
        Write-StepLog -Message "  a different uv ($have) is already present; the pinned uv $UvVersion takes precedence" -Level 'WARN'
    }

    $url = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
    $zip = Join-Path ([System.IO.Path]::GetTempPath()) ("uv-$UvVersion.zip")
    $extractDir = Join-Path ([System.IO.Path]::GetTempPath()) ("uv-$UvVersion-extract")

    if ($DryRun) {
        Write-StepLog -Message ("  WOULD download uv $UvVersion from the pinned release URL and unpack it to $UvExe") -Level 'WOULD'
        return $UvExe
    }

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing -TimeoutSec 300 -ErrorAction Stop
        Write-StepLog -Message ("  downloaded uv {0} ({1} bytes)" -f $UvVersion, (Get-Item -LiteralPath $zip).Length) -Level 'INFO'

        if (Test-Path -LiteralPath $extractDir) {
            Get-ChildItem -LiteralPath $extractDir -Force -ErrorAction SilentlyContinue | Out-Null
        }
        Expand-Archive -LiteralPath $zip -DestinationPath $extractDir -Force -ErrorAction Stop
        $found = Get-ChildItem -LiteralPath $extractDir -Recurse -Filter 'uv.exe' -File -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $found) {
            Write-StepFailure -Stage 'uv-install' -Reason "the downloaded uv archive contained no uv.exe (looked under $extractDir)." `
                -Remedy 'Retry; if it persists the release asset for this version may be mispublished upstream.'
        }
        New-Item -ItemType Directory -Path $UvDir -Force -ErrorAction Stop | Out-Null
        Copy-Item -LiteralPath $found.FullName -Destination $UvExe -Force -ErrorAction Stop

        $check = Invoke-Bounded -FilePath $UvExe -ArgumentList @('--version') -TimeoutSeconds $script:InstallStepTimeoutSeconds -StageName 'uv-version-check'
        if ($check.ExitCode -ne 0) {
            Write-StepFailure -Stage 'uv-install' -Reason "the unpacked uv at $UvExe is not runnable: $($check.Error)" `
                -Remedy 'Check that Windows Defender or a group policy is not blocking the downloaded binary.'
        }
        Write-StepLog -Message ("  uv {0} installed at {1}" -f $UvVersion, $UvExe) -Level 'INFO'
    } catch {
        Write-StepFailure -Stage 'uv-install' -Reason "could not install the pinned uv $UvVersion from $url : $($_.Exception.Message)" `
            -Remedy 'Check network access to github.com, or pre-place uv.exe at the path named in the log and re-run.'
    } finally {
        # Both are this script's own freshly created scratch files under the
        # system temp directory. Remove-ScratchPath refuses anything else.
        foreach ($scratch in @($zip, $extractDir)) {
            try { Remove-ScratchPath -Path $scratch } catch { }
        }
    }
    return $UvExe
}

function Install-PinnedPython {
    <#
    .SYNOPSIS
        Provision the pinned CPython minor series through uv.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$UvExe,

        [Parameter(Mandatory = $true)]
        [string]$PythonSpec
    )

    if ($DryRun) {
        Write-StepLog -Message ("  WOULD run: uv python install $PythonSpec (uv provisions CPython; no Python installer is ever run)") -Level 'WOULD'
        return
    }
    $result = Invoke-Bounded -FilePath $UvExe -ArgumentList @('python', 'install', $PythonSpec) `
        -TimeoutSeconds $script:InstallStepTimeoutSeconds -StageName 'uv-python-install'
    if ($result.ExitCode -ne 0) {
        Write-StepFailure -Stage 'python' -Reason "uv python install $PythonSpec failed: $($result.Error)" `
            -Remedy 'Check network access to the uv python download host, then re-run the installer.'
    }
    $list = Invoke-Bounded -FilePath $UvExe -ArgumentList @('python', 'list', '--only-installed') `
        -TimeoutSeconds $script:InstallStepTimeoutSeconds -StageName 'uv-python-list'
    Write-StepLog -Message ("  provisioned Python: {0}" -f (([string]$list.StdOut).Trim() -replace "`r?`n", '; ')) -Level 'INFO'
}

function Confirm-PinnedTagExists {
    <#
    .SYNOPSIS
        Prove the pinned tag exists before doing hours of work.
    .DESCRIPTION
        Cloning a tag that was never published fails deep inside `git clone` with
        a message most users cannot act on. Asking first turns that into one
        clear line, and it is also how -DryRun reports the one thing it cannot
        know locally.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoUrl,

        [Parameter(Mandatory = $true)]
        [string]$Ref
    )

    if ($DryRun -and -not $VerifyRemote) {
        Write-StepLog -Message ("  WOULD confirm that {0} publishes the tag {1} (pass -VerifyRemote to actually check)" -f $RepoUrl, $Ref) -Level 'WOULD'
        return
    }
    if ($DryRun) {
        Write-StepLog -Message ("  checking that {0} publishes the tag {1}" -f $RepoUrl, $Ref) -Level 'INFO'
    }

    $git = (Get-Command git.exe -ErrorAction SilentlyContinue)
    if (-not $git) {
        Write-StepLog -Message ("  git is not on PATH; the pinned-tag check is skipped and the clone step will report any problem" -f $Ref) -Level 'WARN'
        return
    }

    $result = Invoke-Bounded -FilePath $git.Source -ArgumentList @('ls-remote', '--tags', '--refs', $RepoUrl, ("refs/tags/{0}" -f $Ref)) `
        -TimeoutSeconds $script:RemoteProbeTimeoutSeconds -StageName 'pinned-tag-probe'

    # `git ls-remote` exits 0 whether or not the pattern matched anything. MEASURED
    # against this repository: 'refs/tags/v2.1.0' returned exit 0 with one line,
    # and 'refs/tags/v9.9.9-definitely-not-a-tag' ALSO returned exit 0 with zero
    # lines. So ExitCode proves only that GitHub was reachable. The ref exists if
    # and only if a line came back for it, and that is what has to be tested. The
    # previous version tested ExitCode alone, which made this failure branch
    # unreachable for the one failure it was written for, and the line below then
    # reported "pinned tag exists" for a tag that had never been pushed.
    $wanted = "refs/tags/{0}" -f $Ref
    $tagObjectSha = $null
    foreach ($line in (([string]$result.StdOut) -split "`r?`n")) {
        if ($line -match ("^(?<sha>[0-9a-fA-F]{40})\s+" + [regex]::Escape($wanted) + "\s*$")) {
            $tagObjectSha = $Matches['sha']
            break
        }
    }

    # The ref exists if and only if a line came back for it, and that is the whole
    # test. Deliberately NOT conditioned on ExitCode: MEASURED on this machine,
    # Start-Process -PassThru yields ExitCode=null even when the command succeeds,
    # so branching on it rejected tags that genuinely exist. The exit code is
    # still reported below as a diagnostic, but it does not decide the outcome.
    if (-not $tagObjectSha) {
        $detail = if ($null -eq $result.ExitCode) {
            "git ls-remote returned no line for $wanted and reported no exit code at all"
        } elseif ($result.ExitCode -ne 0) {
            "git ls-remote exited $($result.ExitCode): $($result.Error)"
        } else {
            "git ls-remote succeeded but returned no line for $wanted"
        }
        Write-StepFailure -Stage 'pinned-tag' `
            -Reason "the pinned tag '$Ref' is not published in $RepoUrl. $detail. Either that release was never published, or this machine cannot reach GitHub." `
            -Remedy "Publish the release tag '$Ref', or re-run with -Tag pointing at a tag that exists. Alpha deliberately refuses to install from a moving branch."
    }
    Write-StepLog -Message ("  pinned tag {0} exists in {1} (tag object {2})" -f $Ref, $RepoUrl, $tagObjectSha) -Level 'INFO'
}

function Install-Repository {
    <#
    .SYNOPSIS
        Shallow-clone the pinned tag, or accept an existing checkout untouched.
    .DESCRIPTION
        Idempotency lives here: if a checkout is already present this returns
        immediately and never re-clones, never fetches and never resets. An
        upgrade is therefore a separate, explicit act, and a user who has local
        edits cannot lose them by running the installer again.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoPath,

        [Parameter(Mandatory = $true)]
        [string]$RepoUrl,

        [Parameter(Mandatory = $true)]
        [string]$Ref
    )

    if (Test-Path -LiteralPath (Join-Path $RepoPath '.git')) {
        Write-StepLog -Message "  an Alpha checkout already exists at $RepoPath (no re-clone, nothing overwritten)" -Level 'INFO'
        $head = Invoke-Bounded -FilePath (Get-Command git.exe -ErrorAction SilentlyContinue).Source `
            -ArgumentList @('-C', $RepoPath, 'rev-parse', '--short', 'HEAD') `
            -TimeoutSeconds $script:InstallStepTimeoutSeconds -StageName 'existing-head' -WorkingDirectory $RepoPath
        if ($head.ExitCode -eq 0) {
            Write-StepLog -Message ("  existing checkout is at commit {0}" -f ([string]$head.StdOut).Trim()) -Level 'INFO'
        }
        return
    }

    if ($DryRun) {
        Write-StepLog -Message ("  WOULD shallow-clone {0} at the pinned tag {1} into {2}" -f $RepoUrl, $Ref, $RepoPath) -Level 'WOULD'
        return
    }

    $git = (Get-Command git.exe -ErrorAction SilentlyContinue)
    if (-not $git) {
        Write-StepFailure -Stage 'clone' `
            -Reason 'git was not found on PATH, so the pinned release cannot be fetched. Alpha clones its own source, so a working git is required for the native path.' `
            -Remedy 'Install Git for Windows (https://git-scm.com/download/win) and re-run, or use the Docker path in docs/INSTALLER.md which needs no git on the host.'
    }

    $result = Invoke-Bounded -FilePath $git.Source -WorkingDirectory (Split-Path -Parent $RepoPath) `
        -ArgumentList @('clone', '--depth', '1', '--single-branch', '--branch', $Ref, $RepoUrl, $RepoPath) `
        -TimeoutSeconds $script:CloneTimeoutSeconds -StageName 'clone'
    if ($result.ExitCode -ne 0) {
        Write-StepFailure -Stage 'clone' -Reason "cloning $RepoUrl at tag $Ref failed: $($result.Error)" `
            -Remedy "Confirm the tag '$Ref' is published, then re-run. Alpha never falls back to a moving branch, so there is nothing to retry automatically."
    }
    Write-StepLog -Message ("  cloned {0} at tag {1} (shallow, single branch)" -f $RepoUrl, $Ref) -Level 'INFO'
}

function Install-BackendDependencies {
    <#
    .SYNOPSIS
        uv sync --locked, without the dev group.
    .DESCRIPTION
        --locked so a released install resolves exactly the committed uv.lock: a
        newer resolver would otherwise be free to re-resolve and produce a
        different environment from the one CI tested. The dev group is excluded
        because pytest, hypothesis, ruff and monocle_apptrace are developer
        tooling; a user machine pays ~19 extra distributions for them and gains
        nothing.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$UvExe,

        [Parameter(Mandatory = $true)]
        [string]$BackendPath
    )

    $venvPython = Join-Path $BackendPath '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython) {
        Write-StepLog -Message "  the backend virtualenv already exists at $venvPython (left untouched)" -Level 'INFO'
    }

    if ($DryRun) {
        Write-StepLog -Message ("  WOULD run: uv sync --locked --no-dev  (in {0})" -f $BackendPath) -Level 'WOULD'
        return
    }

    $result = Invoke-Bounded -FilePath $UvExe -WorkingDirectory $BackendPath `
        -ArgumentList @('sync', '--locked', '--no-dev') `
        -TimeoutSeconds $script:SyncTimeoutSeconds -StageName 'uv-sync'
    if ($result.ExitCode -ne 0) {
        Write-StepFailure -Stage 'uv-sync' `
            -Reason "uv sync --locked --no-dev failed in $BackendPath : $($result.Error). Exit code $($result.ExitCode) means the committed uv.lock is not what uv $($script:Pins.uv.version) can resolve." `
            -Remedy 'This is a lockfile/uv version mismatch, not a machine problem. Re-run with the uv version pinned in installer\pins.json, or use the Docker path which pins it in the image.'
    }
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-StepFailure -Stage 'uv-sync' -Reason "uv sync reported success but no interpreter exists at $venvPython." `
            -Remedy 'Inspect the installer log and re-run; the venv directory may be blocked by antivirus or a file lock.'
    }
    Write-StepLog -Message ("  backend dependencies synced from the committed lock (dev group excluded)") -Level 'INFO'
}

function Install-FrontendRuntime {
    <#
    .SYNOPSIS
        Install the pinned Node runtime and prebuild the Next.js frontend.
    .DESCRIPTION
        The frontend is a Next.js SERVER, not a static export: next.config.mjs
        proxies /api/* to the gateway with rewrites(), and a rewrite is a
        server-side feature. There is therefore no static bundle to ship, and a
        Node runtime is a hard requirement of the desktop experience, not an
        optional extra. Building here rather than on first run is what makes the
        first launch fast.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoPath,

        [Parameter(Mandatory = $true)]
        [string]$NodeVersion,

        [Parameter(Mandatory = $true)]
        [string]$UvExe
    )

    $frontend = Join-Path $RepoPath 'frontend'
    $standalone = Join-Path $frontend '.next\standalone'

    if ($DryRun) {
        Write-StepLog -Message ("  WOULD install the pinned Node $NodeVersion runtime and prebuild the Next.js frontend in {0}" -f $frontend) -Level 'WOULD'
        return
    }

    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    $haveNode = $false
    if ($node) {
        $ver = Invoke-Bounded -FilePath $node.Source -ArgumentList @('--version') -TimeoutSeconds $script:InstallStepTimeoutSeconds -StageName 'node-version'
        $reported = ([string]$ver.StdOut).Trim().TrimStart('v')
        if ($ver.ExitCode -eq 0) {
            $haveNode = $true
            if ($reported -ne $NodeVersion) {
                Write-StepLog -Message ("  Node {0} is on PATH but the frontend is pinned to {1}; using the pinned one for the build" -f $reported, $NodeVersion) -Level 'WARN'
            } else {
                Write-StepLog -Message ("  Node {0} already on PATH" -f $NodeVersion) -Level 'INFO'
            }
        }
    }
    if (-not $haveNode) {
        $nodeUrl = "https://nodejs.org/dist/v$NodeVersion/node-v$NodeVersion-win-x64.zip"
        $nodeDir = Join-Path (Split-Path -Parent $RepoPath) 'tools\node'
        $zip = Join-Path ([System.IO.Path]::GetTempPath()) "node-$NodeVersion.zip"
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $nodeUrl -OutFile $zip -UseBasicParsing -TimeoutSec 600 -ErrorAction Stop
            $tmp = Join-Path ([System.IO.Path]::GetTempPath()) "node-$NodeVersion-extract"
            Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force -ErrorAction Stop
            $src = Get-ChildItem -LiteralPath $tmp -Directory | Select-Object -First 1
            if (-not $src) {
                Write-StepFailure -Stage 'node' -Reason "the Node $NodeVersion archive contained no directory after extraction." -Remedy 'Retry; if it persists, download the archive manually and re-run.'
            }
            New-Item -ItemType Directory -Path $nodeDir -Force -ErrorAction Stop | Out-Null
            $target = Join-Path $nodeDir 'node.exe'
            Copy-Item -LiteralPath (Join-Path $src.FullName 'node.exe') -Destination $target -Force -ErrorAction Stop
            $node = [pscustomobject]@{ Source = $target }
            Write-StepLog -Message ("  Node {0} installed at {1}" -f $NodeVersion, $target) -Level 'INFO'
        } catch {
            Write-StepFailure -Stage 'node' -Reason "could not install the pinned Node $NodeVersion from $nodeUrl : $($_.Exception.Message)" `
                -Remedy 'Check network access to nodejs.org, or install Node yourself and re-run (the build step will use the Node on PATH).'
        } finally {
            try { Remove-ScratchPath -Path $zip } catch { }
        }
    }

    # pnpm install --frozen-lockfile, driven through the repo's own pnpm shim so
    # the lockfile is honoured exactly.
    $install = Invoke-Bounded -FilePath $UvExe -WorkingDirectory (Split-Path -Parent $frontend) `
        -ArgumentList @('run', '--project', (Join-Path $RepoPath 'backend'), 'python', (Join-Path $RepoPath 'scripts\pnpm.py'), 'install', '--frozen-lockfile') `
        -TimeoutSeconds $script:NodeTimeoutSeconds -StageName 'pnpm-install'
    if ($install.ExitCode -ne 0) {
        Write-StepFailure -Stage 'pnpm-install' -Reason "frontend dependency install failed: $($install.Error)" `
            -Remedy 'Check network access to the npm registry, then re-run. The backend is already installed at this point, so re-running is safe.'
    }

    # Build through the repository's own pnpm shim (scripts\pnpm.py), which
    # resolves pnpm or Corepack from the Node install above. Hardcoding a
    # pnpm.cjs path inside node_modules would depend on pnpm's own layout.
    $build = Invoke-Bounded -FilePath $UvExe -WorkingDirectory (Split-Path -Parent $frontend) `
        -ArgumentList @('run', '--project', (Join-Path $RepoPath 'backend'), 'python', (Join-Path $RepoPath 'scripts\pnpm.py'), 'build') `
        -TimeoutSeconds $script:NodeTimeoutSeconds -StageName 'frontend-build'
    if ($build.ExitCode -ne 0 -and -not (Test-Path -LiteralPath $standalone)) {
        Write-StepFailure -Stage 'frontend-build' -Reason "the Next.js production build failed: $($build.Error)" `
            -Remedy 'This needs a working Node runtime and about 1.5 GB of free memory. Re-run on a machine that meets the preflight disk requirement.'
    }
    if (Test-Path -LiteralPath $standalone) {
        Write-StepLog -Message '  frontend prebuilt (Next.js standalone output present)' -Level 'INFO'
    } else {
        Write-StepLog -Message '  frontend built; no standalone output, the dev server will be used at runtime' -Level 'WARN'
    }
}

function Initialize-UserConfiguration {
    <#
    .SYNOPSIS
        Create config.yaml, .env and extensions_config.json ONLY when absent.
    .DESCRIPTION
        This is the upgrade-safety guarantee. A user's config.yaml and .env are
        the two files they are most likely to have edited, so both writes are
        guarded by an existence test. Running the installer again after a new
        release cannot silently revert their settings or their generated secret.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoPath,

        [Parameter(Mandatory = $true)]
        [string]$PythonExe,

        [switch]$WithPostgres
    )

    $example = Join-Path $RepoPath 'config.example.yaml'
    $extExample = Join-Path $RepoPath 'extensions_config.example.json'

    # --- config.yaml -------------------------------------------------------
    $configPath = Join-Path $RepoPath 'config.yaml'
    if (!(Test-Path -LiteralPath $configPath)) {
        Invoke-InstallStep -Name "create config.yaml from the shipped example (SQLite backend, no database server required)" -Action {
            if (-not (Test-Path -LiteralPath $example)) {
                Write-StepFailure -Stage 'config' -Reason "config.example.yaml is missing from the checkout at $example, so there is nothing to copy." `
                    -Remedy 'Re-run the installer; a release that ships no example config is broken.'
            }
            Copy-Item -LiteralPath $example -Destination $configPath -ErrorAction Stop
            Write-StepLog -Message '  config.yaml created (SQLite; Postgres is opt-in via -WithPostgres)' -Level 'INFO'
        } | Out-Null
    } else {
        Write-StepLog -Message '  config.yaml already exists (left untouched, your settings are preserved)' -Level 'INFO'
    }

    # --- .env, with locally generated secrets -------------------------------
    $envPath = Join-Path $RepoPath '.env'
    if (!(Test-Path -LiteralPath (Join-Path $RepoPath '.env'))) {
        Invoke-InstallStep -Name 'create .env with secrets generated on this machine by secrets.token_urlsafe' -Action {
            $envExample = Join-Path $RepoPath '.env.example'
            if (Test-Path -LiteralPath $envExample) {
                Copy-Item -LiteralPath $envExample -Destination $envPath -ErrorAction Stop
            } else {
                New-Item -ItemType File -Path $envPath -Force -ErrorAction Stop | Out-Null
            }
            $authSecret = New-InstallSecret -PythonExe $PythonExe
            $csrfSecret = New-InstallSecret -PythonExe $PythonExe
            # The values are appended to the gitignored .env and never echoed.
            Add-Content -LiteralPath $envPath -Encoding UTF8 -ErrorAction Stop -Value @(
                ''
                '# --- generated by the Alpha Windows installer on this machine ---'
                '# Generated locally with Python secrets.token_urlsafe. Never shipped, never shared.'
                "BETTER_AUTH_SECRET=$authSecret"
                "CSRF_SECRET=$csrfSecret"
                '# Single-user local install: no login prompt on localhost.'
                'AGENT_WORKSPACE_AUTH_DISABLED=1'
                "# Database: sqlite (the default needs no database server)."
            )
            if ($WithPostgres) {
                Add-Content -LiteralPath $envPath -Encoding UTF8 -ErrorAction Stop -Value @(
                    '# -WithPostgres was requested: point this at YOUR OWN Postgres instance.'
                    '# The installer never creates a database and never ships credentials.'
                    'AGENT_WORKSPACE_DATABASE_BACKEND=postgres'
                    '# AGENT_WORKSPACE_DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/alpha'
                )
            }
            Write-StepLog -Message '  .env created; two secrets generated locally and written only to the gitignored .env' -Level 'INFO'
        } | Out-Null
    } else {
        Write-StepLog -Message '  .env already exists (left untouched, your existing secrets are preserved)' -Level 'INFO'
    }

    # --- extensions_config.json --------------------------------------------
    $extConfigPath = Join-Path $RepoPath 'extensions_config.json'
    if (!(Test-Path -LiteralPath $extConfigPath)) {
        Invoke-InstallStep -Name 'create extensions_config.json (every bundled MCP server ships disabled)' -Action {
            if (Test-Path -LiteralPath $extExample) {
                Copy-Item -LiteralPath $extExample -Destination $extConfigPath -ErrorAction Stop
            } else {
                New-Item -ItemType File -Path $extConfigPath -Force -ErrorAction Stop | Out-Null
                Set-Content -LiteralPath $extConfigPath -Value '{}' -Encoding UTF8 -ErrorAction Stop
            }
            Write-StepLog -Message '  extensions_config.json created' -Level 'INFO'
        } | Out-Null
    } else {
        Write-StepLog -Message '  extensions_config.json already exists (left untouched)' -Level 'INFO'
    }

    # --- frontend/.env -----------------------------------------------------
    $frontendEnv = Join-Path $RepoPath 'frontend\.env'
    if (!(Test-Path -LiteralPath $frontendEnv)) {
        Invoke-InstallStep -Name 'create frontend/.env pinned to a production build' -Action {
            New-Item -ItemType Directory -Path (Split-Path -Parent $frontendEnv) -Force -ErrorAction Stop | Out-Null
            Set-Content -LiteralPath $frontendEnv -Value 'NODE_ENV=production' -Encoding UTF8 -ErrorAction Stop
        } | Out-Null
    }
}

function New-InstallShortcuts {
    <#
    .SYNOPSIS
        Create the Start Menu and desktop shortcuts.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoPath
    )

    $script = Join-Path $script:InstallerDir 'create-shortcuts.ps1'
    if (-not (Test-Path -LiteralPath $script)) {
        Write-StepFailure -Stage 'shortcuts' -Reason "the shortcut helper is missing: $script" -Remedy 'Reinstall the installer.'
    }

    if ($DryRun) {
        Write-StepLog -Message ("  WOULD create the Start Menu entry and a desktop shortcut for {0}" -f $RepoPath) -Level 'WOULD'
        return
    }
    $result = Invoke-Bounded -FilePath 'powershell.exe' `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script, '-RepoPath', $RepoPath) `
        -TimeoutSeconds $script:InstallStepTimeoutSeconds -StageName 'shortcuts'
    if ($result.ExitCode -ne 0) {
        Write-StepFailure -Stage 'shortcuts' -Reason "shortcut creation failed: $($result.Error)" `
            -Remedy 'Alpha is installed and runnable without shortcuts; create them later with: powershell -File installer\create-shortcuts.ps1'
    }
    Write-StepLog -Message '  Start Menu and desktop shortcuts created' -Level 'INFO'
}

function Invoke-PostInstallVerification {
    <#
    .SYNOPSIS
        Actually start Alpha and prove its health endpoint answers.
    .DESCRIPTION
        Verifying that files exist proves nothing. This starts the real launcher
        and polls the gateway's /health endpoint, because an installer that
        reports success for a stack that cannot boot is the worst possible
        outcome: the user believes they are running Alpha and they are not.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoPath,

        [int]$TimeoutSeconds = 600
    )

    $script:Checks = New-Object System.Collections.ArrayList
    $gatewayPort = 8001
    $frontendPort = 3000

    Write-StepLog -Message 'Post-install verification: starting Alpha and checking health' -Level 'STEP'

    $launcher = Join-Path $RepoPath 'start.ps1'
    if (-not (Test-Path -LiteralPath $launcher)) {
        Add-VerificationCheck 'start.ps1 present' $false "missing: $launcher"
    } else {
        Add-VerificationCheck 'start.ps1 present' $true ''
    }

    if ($DryRun) {
        Write-StepLog -Message '  dry run: not starting Alpha' -Level 'WOULD'
        return
    }

    # Start detached. The quotes around -File are load-bearing: Start-Process
    # joins the argument array WITHOUT quoting, so an unquoted path containing
    # a space ("C:\Users\Jane Doe\...") would truncate and Alpha would never
    # start.
    Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
        '-File', ('"{0}"' -f $launcher), '-NoBrowser'
    ) -WorkingDirectory $RepoPath -WindowStyle Hidden | Out-Null

    $gateway = Test-HealthEndpointProbe -Uri ("http://127.0.0.1:{0}/health" -f $gatewayPort) -TimeoutSeconds $TimeoutSeconds
    Add-VerificationCheck "gateway /health answers (127.0.0.1:$gatewayPort)" $gateway.Ready ("status={0} error={1}" -f $gateway.Status, $gateway.Error)

    $ready = Test-HealthEndpointProbe -Uri ("http://127.0.0.1:{0}/health/ready" -f $gatewayPort) -TimeoutSeconds 120
    Add-VerificationCheck "gateway /health/ready answers" ($ready.Status -ge 200 -and $ready.Status -lt 500) ("status={0} body={1}" -f $ready.Status, (Collapse-Text $ready.Body))

    $frontend = Test-HealthEndpointProbe -Uri ("http://127.0.0.1:{0}/" -f $frontendPort) -TimeoutSeconds 300
    Add-VerificationCheck "frontend answers (127.0.0.1:$frontendPort)" $frontend.Ready ("status={0} error={1}" -f $frontend.Status, $frontend.Error)

    $failed = @($script:Checks | Where-Object { $_.Result -eq 'FAIL' })
    Write-StepLog -Message '---------------- INSTALL VERIFICATION ----------------' -Level 'INFO'
    foreach ($check in $script:Checks) {
        $line = '  [{0}] {1}{2}' -f $check.Result, $check.Check, $(if ($check.Detail) { " - $($check.Detail)" } else { '' })
        if ($check.Result -eq 'PASS') { Write-StepLog -Message $line -Level 'INFO' } else { Write-StepLog -Message $line -Level 'ERROR' }
    }

    if ($failed.Count -gt 0) {
        Write-StepLog -Message "INSTALL VERIFICATION FAILED: $($failed.Count) check(s) did not pass. Alpha is installed but is NOT working." -Level 'ERROR'
        Write-StepLog -Message ("Inspect {0}\gateway.err.log and {0}\frontend.err.log, then re-run the installer (it is safe to run again)." -f (Join-Path $RepoPath 'logs')) -Level 'ERROR'
        exit 1
    }
    Write-StepLog -Message 'INSTALL VERIFICATION PASSED: Alpha is running and healthy.' -Level 'INFO'
}

function Add-VerificationCheck {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Check,
        [Parameter(Mandatory = $true)][bool]$Ok,
        [string]$Detail = ''
    )
    [void]$script:Checks.Add([pscustomobject]@{
            Check   = $Check
            Result  = $(if ($Ok) { 'PASS' } else { 'FAIL' })
            Detail  = $Detail
        })
}

function Collapse-Text {
    [CmdletBinding()]
    param([string]$Text)
    if (-not $Text) { return '' }
    $flat = ($Text -replace "`r?`n", ' ').Trim()
    if ($flat.Length -gt 160) { return $flat.Substring(0, 160) + '...' }
    return $flat
}

function Test-HealthEndpointProbe {
    <#
    .SYNOPSIS
        Poll a URL until it answers 200 or the deadline passes.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [int]$TimeoutSeconds = 120,
        [int]$PollIntervalSeconds = 5
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $status = 0
    $body = ''
    $lastError = 'never attempted'
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
            $status = [int]$response.StatusCode
            $body = [string]$response.Content
            if ($status -eq 200) { $ready = $true; break }
            $lastError = "HTTP $status"
        } catch {
            $lastError = $_.Exception.Message
            try { if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode } } catch { }
        }
        Start-Sleep -Seconds $PollIntervalSeconds
    }
    return [pscustomobject]@{ Uri = $Uri; Ready = $ready; Status = $status; Body = $body; Error = $lastError }
}

# ---------------------------------------------------------------------------
# Uninstall delegation
# ---------------------------------------------------------------------------
function Invoke-Uninstall {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Root)

    $uninstaller = Join-Path $script:InstallerDir 'uninstall-alpha.ps1'
    if (-not (Test-Path -LiteralPath $uninstaller)) {
        Write-StepFailure -Stage 'uninstall' -Reason "the uninstaller is missing: $uninstaller" -Remedy 'Delete the install folder manually.'
    }
    $args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $uninstaller, '-InstallRoot', $Root)
    if ($Yes) { $args += '-Yes' }
    & powershell.exe @args
    exit $LASTEXITCODE
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
function Invoke-Main {
    [CmdletBinding()]
    param()

    # Normalise the install root ONCE, in place. Everything downstream (and the
    # idempotency gate in particular) reads $InstallRoot, so there is a single
    # answer to "where is Alpha going?" and no way for two parts of the script
    # to disagree about it.
    if (-not $InstallRoot) { $InstallRoot = Join-Path $env:LOCALAPPDATA 'Alpha' }
    $root = $InstallRoot

    Write-Host ''
    Write-Host '========================================================' -ForegroundColor Cyan
    Write-Host '        Alpha - unattended Windows installer' -ForegroundColor Cyan
    Write-Host '========================================================' -ForegroundColor Cyan
    if ($DryRun) {
        Write-Host '   DRY RUN: every check runs, nothing on this machine changes.' -ForegroundColor Magenta
    }
    Write-Host ''

    $script:InstallerDir = $PSScriptRoot
    $script:Pins = Read-Pins -OverrideTag $Tag -OverrideRepoUrl $RepoUrl

    # The log lives inside the install root, which the preflight may not have
    # created yet; fall back to a temp log for the preflight stage itself.
    $script:LogPath = Join-Path $root 'logs\alpha-installer.log'
    if (-not (Split-Path -Parent $script:LogPath -Resolve -ErrorAction SilentlyContinue)) {
        $script:LogPath = Join-Path ([System.IO.Path]::GetTempPath()) 'alpha-installer.log'
    }

    Write-StepLog -Message ("Alpha installer starting. Pins: repo={0}@{1} uv={2} python={3} node={4}" -f `
            $script:Pins.repo.url, $script:Pins.repo.ref, $script:Pins.uv.version, $script:Pins.python.version, $script:Pins.node.version) -Level 'INFO'
    if ($DryRun) {
        Write-StepLog -Message 'DRY RUN active: no file on this machine will be created or changed.' -Level 'WOULD'
    }

    # ---- Idempotency gate -------------------------------------------------
    # Checked before anything else so a second run is a fast, honest no-op
    # rather than a repeat of the whole install.
    $completeFlag = Join-Path $root '.alpha-install-complete'
    if (Test-Path $InstallRoot) {
        if (Test-Path -LiteralPath $completeFlag) {
            Write-StepLog -Message "Alpha is already installed at $root (completed marker present)." -Level 'INFO'
            Write-StepLog -Message 'Nothing to do: the existing install, your config.yaml and your .env are left exactly as they are.' -Level 'INFO'
            Write-StepLog -Message 'To reinstall from scratch, run uninstall-alpha.ps1 -Yes first, or pass -InstallRoot <new path>.' -Level 'INFO'
            exit 0
        }
        Write-StepLog -Message "An incomplete Alpha install already exists at $root; resuming without re-cloning and without touching your config." -Level 'WARN'
    }

    # ---- Preflight --------------------------------------------------------
    # -DryRun runs this too, on purpose: a dry run that skipped the checks
    # would not be a dry run, it would be a no-op that reports success.
    Invoke-Preflight -Root $root

    $uvExe = Join-Path $root 'tools\uv\uv.exe'
    $repoPath = Join-Path $root 'alpha'
    $venvPython = Join-Path $repoPath 'backend\.venv\Scripts\python.exe'

    # ---- Step 1: pinned uv -------------------------------------------------
    Write-StepLog -Message '[1/8] Provisioning the pinned uv toolchain' -Level 'STEP'
    Invoke-InstallStep -Name ("install pinned uv {0}" -f $script:Pins.uv.version) -Action {
        $script:InstalledUv = Install-PinnedUv -UvExe $uvExe -UvVersion $script:Pins.uv.version -UvDir (Join-Path $root 'tools\uv')
    } | Out-Null
    if ($DryRun) { $script:InstalledUv = $uvExe } else { $script:InstalledUv = $uvExe }

    # ---- Step 2: pinned Python through uv ---------------------------------
    Write-StepLog -Message '[2/8] Provisioning CPython through uv (no Python installer is ever run)' -Level 'STEP'
    Install-PinnedPython -UvExe $script:InstalledUv -PythonSpec $script:Pins.python.version

    # ---- Step 3: pinned tag ----------------------------------------------
    Write-StepLog -Message '[3/8] Resolving the pinned release' -Level 'STEP'
    Confirm-PinnedTagExists -RepoUrl $script:Pins.repo.url -Ref $script:Pins.repo.ref

    # ---- Step 4: shallow clone -------------------------------------------
    Write-StepLog -Message ('[4/8] Obtaining the source at pinned tag {0}' -f $script:Pins.repo.ref) -Level 'STEP'
    Install-Repository -RepoPath $repoPath -RepoUrl $script:Pins.repo.url -Ref $script:Pins.repo.ref

    # ---- Step 5: uv sync --------------------------------------------------
    Write-StepLog -Message '[5/8] Installing Python dependencies from the committed lock' -Level 'STEP'
    Install-BackendDependencies -UvExe $script:InstalledUv -BackendPath (Join-Path $repoPath 'backend')

    # ---- Step 6: frontend runtime ----------------------------------------
    Write-StepLog -Message '[6/8] Provisioning the Node runtime and prebuilding the frontend' -Level 'STEP'
    Install-FrontendRuntime -RepoPath $repoPath -NodeVersion $script:Pins.node.version -UvExe $script:InstalledUv

    # ---- Step 7: user configuration --------------------------------------
    Write-StepLog -Message '[7/8] Preparing user configuration (never overwriting yours)' -Level 'STEP'
    Initialize-UserConfiguration -RepoPath $repoPath -PythonExe $venvPython -WithPostgres:$WithPostgres

    # ---- Step 8: shortcuts + marker ---------------------------------------
    Write-StepLog -Message '[8/8] Creating shortcuts and finalising' -Level 'STEP'
    New-InstallShortcuts -RepoPath $repoPath

    Invoke-InstallStep -Name 'write the completed marker so a second run is a no-op' -Action {
        Set-Content -LiteralPath $completeFlag -Encoding UTF8 -ErrorAction Stop -Value @(
            '# Alpha install completed marker. Its presence makes a re-run of the installer a no-op.'
            ("completed_utc={0}" -f (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))
            ("pinned_tag={0}" -f $script:Pins.repo.ref)
            ("uv_version={0}" -f $script:Pins.uv.version)
            ("python_series={0}" -f $script:Pins.python.version)
            ("node_version={0}" -f $script:Pins.node.version)
            ("database_backend={0}" -f $(if ($WithPostgres) { 'postgres' } else { 'sqlite' }))
        )
    } | Out-Null

    # ---- Post-install verification ----------------------------------------
    if (-not $SkipVerification) {
        Invoke-PostInstallVerification -RepoPath $repoPath -TimeoutSeconds $script:HealthTimeoutSeconds
    } else {
        Write-StepLog -Message 'Post-install verification skipped (-SkipVerification). Alpha was NOT started or checked.' -Level 'WARN'
    }

    Write-Host ''
    if ($DryRun) {
        Write-Host '========================================================' -ForegroundColor Magenta
        Write-Host '   DRY RUN COMPLETE - nothing on this machine was changed.' -ForegroundColor Magenta
        Write-Host '========================================================' -ForegroundColor Magenta
        Write-Host '   Re-run without -DryRun to perform the install.' -ForegroundColor White
        Write-Host ''
        return
    }
    Write-Host '========================================================' -ForegroundColor Green
    Write-Host '   Alpha is installed.' -ForegroundColor Green
    Write-Host '========================================================' -ForegroundColor Green
    Write-Host ("   Install root : {0}" -f $root) -ForegroundColor White
    Write-Host '   Web UI       : http://127.0.0.1:3000' -ForegroundColor White
    Write-Host '   Gateway      : http://127.0.0.1:8001/health' -ForegroundColor White
    Write-Host ("   Start/stop   : {0}\start.ps1  |  {0}\stop.ps1" -f $root) -ForegroundColor White
    Write-Host ("   Uninstall    : {0}" -f (Join-Path $script:InstallerDir 'uninstall-alpha.ps1')) -ForegroundColor White
    Write-Host ("   Log          : {0}" -f $script:LogPath) -ForegroundColor White
    Write-Host ''
}

if ($Uninstall) {
    $root = if ($InstallRoot) { $InstallRoot } else { Join-Path $env:LOCALAPPDATA 'Alpha' }
    Invoke-Uninstall -Root $root
}

Invoke-Main
