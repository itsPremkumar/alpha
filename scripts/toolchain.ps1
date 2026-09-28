# Alpha - project-local toolchain bootstrap (Windows / PowerShell)
#
# Dot-sourced by `install.ps1` and `start.ps1`. This file is the ONLY place that
# decides where Alpha's external tools live and in what order they are resolved.
# Both launchers used to carry their own private copy of that logic, so a fix to
# one did not reach the other -- which is how a candidate list naming another
# product's private `bin` directory survived in both at the same time.
#
#   . "$RepoRoot\scripts\toolchain.ps1"
#   Initialize-AlphaToolchain
#   $uvPath   = Resolve-AlphaUv
#   $nodePath = Resolve-AlphaNode
#
# Two rules, both load-bearing:
#
#   1. PROJECT-LOCAL. Everything Alpha downloads for itself stays inside the
#      checkout, under `.tools/`. uv's own defaults are all per-user globals --
#      measured on the machine this was written for: `uv cache dir` resolved to
#      `%LOCALAPPDATA%\uv\cache`, `uv python dir` to `%APPDATA%\uv\python` and
#      `uv tool dir` to `%APPDATA%\uv\tools`. Unpinned, every clone on the
#      machine shares one cache, `uv tool install` writes outside the repo, and
#      deleting the checkout strands hundreds of megabytes in the profile.
#
#   2. MACHINE-INDEPENDENT. No path below is absolute and none is specific to
#      one machine or one vendor. The root comes from `$PSScriptRoot`, so the
#      checkout can be cloned, moved or renamed.
#
# Enforced by `backend/tests/test_portable_paths.py`.

$ErrorActionPreference = "Stop"

# `<repo>/.tools` -- derived from this file's own location, never from a literal.
$ToolchainRoot   = Join-Path (Split-Path $PSScriptRoot -Parent) ".tools"
$UvBinDir        = Join-Path $ToolchainRoot "bin"
$UvCacheDir      = Join-Path $ToolchainRoot "uv-cache"
$UvPythonDir     = Join-Path $ToolchainRoot "python"
$UvToolDir       = Join-Path $ToolchainRoot "uv-tools"
$PnpmStoreDir    = Join-Path $ToolchainRoot "pnpm-store"
$BackendVenvDir  = Join-Path (Split-Path $PSScriptRoot -Parent) "backend\.venv"

# Every uv directory is pinned inside the checkout, and is *exported* from a value
# computed here rather than read back from the environment. Re-reading
# `$env:UV_*` would let a stale inherited value put the directory back outside
# the project, which is the failure this whole block exists to prevent -- and
# `$env:UV_INSTALL_DIR` in particular is what makes the official installer drop
# the binary here instead of into the user profile.
$env:UV_INSTALL_DIR         = $UvBinDir
$env:UV_CACHE_DIR           = $UvCacheDir
$env:UV_PYTHON_INSTALL_DIR  = $UvPythonDir
$env:UV_TOOL_DIR            = $UvToolDir
$env:UV_TOOL_BIN_DIR        = $UvBinDir
$env:UV_PROJECT_ENVIRONMENT = $BackendVenvDir

# pnpm's content-addressable store is a per-user global by default
# (`%LOCALAPPDATA%\pnpm\store`). `frontend/.npmrc` pins the relative equivalent
# for the `pnpm install` path; this covers any pnpm invoked from a launcher.
$env:npm_config_store_dir = $PnpmStoreDir

function Initialize-AlphaToolchain {
    <#
      Create the project-local directories. Safe to call repeatedly: every call
      is a no-op once the tree exists, and it never touches a profile location.
    #>
    foreach ($dir in @($ToolchainRoot, $UvBinDir, $UvCacheDir, $UvPythonDir, $UvToolDir)) {
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
    }
}

function Resolve-AlphaTool {
    <#
      Resolve an external tool in a fixed, deterministic order:
        1. the copy Alpha downloaded into THIS checkout (.tools\bin)
        2. PATH
        3. an explicit, vendor-neutral list of conventional install directories

      Step 1 precedes step 2 on purpose: otherwise a stale system-wide `uv`
      shadows the pinned one and the project-local guarantee is cosmetic.

      There is deliberately NO recursive scan of the user profile here. The
      previous implementation fell back to `Get-ChildItem -Recurse -Depth 2`
      over `%LOCALAPPDATA%` and `%ProgramFiles%` and took the first hit.
      `Get-ChildItem` does not guarantee an order, so the same machine could
      resolve a different binary after an unrelated install, the scan walked a
      large part of the profile on every launch, and -- measured here -- it is
      how Alpha came to run a `uv.exe` belonging to a different product.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$WellKnownDirs = @()
    )

    # 1. Project-local copy.
    foreach ($ext in @(".exe", "")) {
        $local = Join-Path $UvBinDir "$Name$ext"
        if (Test-Path $local -PathType Leaf) { return $local }
    }

    # 2. PATH. "<name>.exe" is resolved explicitly first because Windows resolves
    #    a bare name through PATHEXT, and some managed images ship
    #    PATHEXT=.CPL, which makes `Get-Command uv` fail even when uv.exe is
    #    installed and its directory is on PATH.
    foreach ($candidate in @("$Name.exe", $Name)) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source) { return $cmd.Source }
    }

    # 3. Conventional install directories, in a fixed order.
    foreach ($dir in $WellKnownDirs) {
        if (-not $dir) { continue }
        $hit = Join-Path $dir "$Name.exe"
        if (Test-Path $hit -PathType Leaf) { return $hit }
    }

    return $null
}

function Get-AlphaUvWellKnownDirs {
    <#
      Generic, vendor-neutral locations a `uv` may already live in. Every entry
      is a whole directory that is checked by exact path.

      A private `bin` directory belonging to an unrelated third-party tool is
      NOT a candidate. Listing one means Alpha silently adopts that product's
      binary: on the machine this was written for, the only `uv` on the system
      was another tool's, and `Get-Command uv` returned that copy.
    #>
    @(
        "$env:USERPROFILE\.cargo\bin",
        "$env:USERPROFILE\.local\bin",
        "$env:USERPROFILE\scoop\shims",
        "$env:LOCALAPPDATA\Programs\uv",
        "$env:APPDATA\uv",
        "$env:ProgramData\chocolatey\bin"
    )
}

function Get-AlphaNodeWellKnownDirs {
    <#
      Conventional Node.js install directories.

      nvm names its directory after the version it currently holds, so a pinned
      candidate resolves only on the machine that happens to have exactly that
      version. The nvm root is enumerated instead, newest first, so a version
      switch cannot silently drop Node out of the resolution order.
    #>
    $dirs = @(
        "$env:ProgramFiles\nodejs",
        "${env:ProgramFiles(x86)}\nodejs",
        "$env:LOCALAPPDATA\Programs\nodejs",
        "$env:LOCALAPPDATA\nvm4w\nodejs"
    )

    $nvmRoot = "$env:APPDATA\nvm"
    if (Test-Path $nvmRoot -PathType Container) {
        $versioned = Get-ChildItem -LiteralPath $nvmRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^v\d+\.\d+\.\d+$' } |
            Sort-Object { [version]($_.Name.TrimStart('v')) } -Descending
        foreach ($d in $versioned) { $dirs += $d.FullName }
    }

    $dirs
}

function Get-AlphaNginxWellKnownDirs {
    <#
      Conventional nginx install directories.

      nginx on Windows unpacks to a versioned directory holding conf/, html/
      and logs/, with the executable in its sbin/. Only directories whose shape
      actually matches that layout are offered, so an unrelated `sbin` on PATH
      is not mistaken for a server.
    #>
    @(
        "$env:ProgramFiles\nginx",
        "${env:ProgramFiles(x86)}\nginx",
        "$env:LOCALAPPDATA\nginx",
        "$env:ProgramData\nginx"
    )
}

function Resolve-AlphaUv {
    (Resolve-AlphaTool -Name "uv" -WellKnownDirs (Get-AlphaUvWellKnownDirs))
}

function Resolve-AlphaNode {
    (Resolve-AlphaTool -Name "node" -WellKnownDirs (Get-AlphaNodeWellKnownDirs))
}

function Resolve-AlphaNginx {
    <#
      Locate nginx, project-local copy first.

      The project-local copy is a directory rather than a single binary: the
      official Windows zip unpacks to a tree with conf/, html/ and sbin/, and
      serve.sh launches it with `-p <prefix>` so its own conf/ and logs/ are
      used. Resolve-AlphaTool cannot express that, so this is a separate
      resolver that returns the sbin/nginx.exe path (what the caller needs to
      build a command) after confirming the surrounding prefix exists.

      Step 1 precedes PATH for the same reason as Resolve-AlphaTool: a stale
      system-wide nginx must not shadow the pinned one.
    #>
    $candidates = @(
        (Join-Path $ToolchainRoot "nginx"),
        (Join-Path $UvBinDir "nginx")
    )
    foreach ($dir in $candidates) {
        $exe = Join-Path $dir "sbin\nginx.exe"
        if (Test-Path $exe -PathType Leaf) { return $exe }
        # A flat layout (exe copied next to its conf/) is also accepted.
        $flat = Join-Path $dir "nginx.exe"
        if (Test-Path $flat -PathType Leaf) { return $flat }
    }

    $cmd = Get-Command "nginx.exe" -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { return $cmd.Source }

    foreach ($dir in (Get-AlphaNginxWellKnownDirs)) {
        if (-not $dir) { continue }
        $exe = Join-Path $dir "sbin\nginx.exe"
        if (Test-Path $exe -PathType Leaf) { return $exe }
        $flat = Join-Path $dir "nginx.exe"
        if (Test-Path $flat -PathType Leaf) { return $flat }
    }

    return $null
}

function Get-AlphaPinnedVersion {
    <#
      Read one pinned version out of installer/pins.json. The pins file is the
      single source of truth for every version the installer downloads, and
      installer/tests/test_installer_contract.py enforces that contract, so
      nothing here hardcodes a version.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Name
    )

    $pinsPath = Join-Path (Split-Path $PSScriptRoot -Parent) "installer\pins.json"
    if (-not (Test-Path $pinsPath -PathType Leaf)) { return $null }
    try {
        $pins = Get-Content $pinsPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    } catch {
        return $null
    }
    $entry = $pins.$Name
    if ($null -eq $entry) { return $null }
    if ($entry -is [string]) { return $entry }
    return $entry.version
}

function Install-AlphaNginx {
    <#
      Download the official nginx Windows build into THIS checkout.

      Optional by contract. The caller must treat a failure here as a warning,
      not a failure: scripts/serve.sh treats nginx as optional because the
      Gateway (:8001) and the frontend (:3000) each serve their own port, so a
      machine that cannot download nginx still gets a working Alpha. The only
      thing it loses is the unified :2026 entry point.

      The archive is expanded under .tools/ rather than installed system-wide:
      it needs no administrator rights, it cannot collide with another
      nginx on the machine, and deleting the checkout removes it.
    #>
    Initialize-AlphaToolchain

    if (Resolve-AlphaNginx) { return (Resolve-AlphaNginx) }

    $series = Get-AlphaPinnedVersion -Name "nginx"
    if (-not $series) {
        Write-Warning "installer/pins.json has no nginx entry; skipping nginx."
        return $null
    }

    $base = "https://nginx.org/download/"
    $index = $null
    try {
        $index = Invoke-WebRequest -Uri $base -UseBasicParsing -TimeoutSec 60 -ErrorAction Stop
    } catch {
        Write-Warning "Could not reach $base to discover an nginx build ($_). Skipping nginx."
        return $null
    }

    # nginx names its downloads `nginx-<major>.<minor>.<patch>.zip`, and the
    # index offers every patch in a series, so the pin carries the minor series
    # and the highest available patch is taken here. Matching the filename
    # shape rather than a path is deliberate: the index links both
    # `nginx-1.27.5.zip` and a `nginx-1.27.5/` directory, and only the former
    # is the Windows build.
    $pattern = "nginx-($([regex]::Escape($series))\.\d+)\.zip"
    $match = [regex]::Matches($index.Content, $pattern) |
        Sort-Object { [version]$_.Groups[1].Value } -Descending |
        Select-Object -First 1
    if (-not $match) {
        Write-Warning "No nginx $series Windows build found at $base. Skipping nginx."
        return $null
    }

    $fileName = Split-Path $match.Value -Leaf
    $zipPath = Join-Path $ToolchainRoot $fileName
    $dest    = Join-Path $ToolchainRoot "nginx"

    New-Item -ItemType Directory -Path $ToolchainRoot -Force | Out-Null
    try {
        Write-Host "  -> Downloading $fileName ..."
        Invoke-WebRequest -Uri ($base + $fileName) -OutFile $zipPath -UseBasicParsing -TimeoutSec 600 -ErrorAction Stop
        if (Test-Path $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
        # The zip contains a top-level `nginx-<version>/` directory; expand and
        # lift that directory so the prefix is `.tools/nginx`, not
        # `.tools/nginx/nginx-<version>`.
        $stage = Join-Path $ToolchainRoot "_nginx-stage"
        if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
        Expand-Archive -LiteralPath $zipPath -DestinationPath $stage -Force
        $inner = Get-ChildItem -LiteralPath $stage -Directory | Select-Object -First 1
        if ($inner) { Move-Item -LiteralPath $inner.FullName -Destination $dest -Force }
        else { Move-Item -LiteralPath $stage -Destination $dest -Force }
        if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
        Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
    } catch {
        Write-Warning "nginx download/extract failed ($_). Skipping nginx; Alpha will run without the :2026 entry point."
        return $null
    }

    $resolved = Resolve-AlphaNginx
    if ($resolved) { Write-Host "  [OK] nginx installed at $resolved" -ForegroundColor Green }
    return $resolved
}

function Install-AlphaUv {
    <#
      Download uv into THIS checkout.

      `$env:UV_INSTALL_DIR` is already exported from a value computed above,
      which is the variable the official installer honours, so the binary lands
      inside the project rather than in the user profile. Nothing else about the
      upstream installer is changed.
    #>
    Initialize-AlphaToolchain

    # The installer's own progress output is not suppressed: a silent download
    # that then fails to produce a binary is indistinguishable from a hang.
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"

    $resolved = Resolve-AlphaUv
    if ($resolved) { $env:PATH = (Split-Path $resolved) + ";" + $env:PATH }
    return $resolved
}
