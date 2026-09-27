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

function Resolve-AlphaUv {
    (Resolve-AlphaTool -Name "uv" -WellKnownDirs (Get-AlphaUvWellKnownDirs))
}

function Resolve-AlphaNode {
    (Resolve-AlphaTool -Name "node" -WellKnownDirs (Get-AlphaNodeWellKnownDirs))
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
