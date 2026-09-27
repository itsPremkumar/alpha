"""The Windows toolchain must be project-local and machine-independent.

Alpha resolves three external tools at startup -- ``uv``, Node.js and pnpm --
and every one of them has a default install/cache location that lives in the
*user profile*, shared with every other project on the machine. The launcher
therefore has to say two things out loud, and this module is the gate that
holds it to them:

1. **Project-local.** Nothing Alpha downloads for itself may land outside the
   checkout. ``uv``'s installer, its wheel cache, its managed CPython and its
   ``tool install`` directory each default to a per-user global path, so
   leaving them unset means two clones of Alpha fight over one cache, and
   deleting the checkout leaves hundreds of megabytes behind in the profile.

2. **Machine-independent.** No tracked file may name this machine, another
   machine, or one specific vendor's private tool directory. The launcher used
   to list ``%LOCALAPPDATA%\\hermes\\bin\\uv.exe`` as a ``uv`` candidate, so on
   the machine this was written on, Alpha silently ran *another tool's* ``uv``
   (measured: ``Get-Command uv`` -> ``...\\AppData\\Local\\hermes\\bin\\uv.exe``).
   A last-resort ``Get-ChildItem -Recurse`` over ``%LOCALAPPDATA%`` and
   ``%ProgramFiles%`` then widened that to "any ``uv.exe``/``node.exe`` anywhere
   under the profile", in an unspecified order.

``scripts/toolchain.ps1`` is the single source of truth: it pins all five uv
directories inside ``.tools/`` and both launchers dot-source it. Each detector
below carries a positive control, so a scan whose regex quietly stops matching
fails here instead of passing forever by having stopped looking.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# The shared module plus the two launchers that dot-source it.
TOOLCHAIN_MODULE = "scripts/toolchain.ps1"
LAUNCHERS = ["install.ps1", "start.ps1"]
TOOLCHAIN_FILES = [TOOLCHAIN_MODULE, *LAUNCHERS]

# Files that decide where a tool installs, caches, or downloads.
INSTALL_SURFACE = [*TOOLCHAIN_FILES, "Makefile", "frontend/.npmrc", ".gitignore"]

# Third-party tool directories that must never appear in a candidate list.
# Each is a private install location belonging to some *other* product; a
# launcher that lists one adopts that product's binary on any machine that
# happens to have it.
FOREIGN_VENDOR_TOOLS = ["hermes"]

# uv's own defaults when UV_* is unset. Every one of these is a per-user global
# path, which is the exact thing this gate forbids.
UV_GLOBAL_DEFAULTS = (
    "UV_INSTALL_DIR",
    "UV_CACHE_DIR",
    "UV_PYTHON_INSTALL_DIR",
    "UV_TOOL_DIR",
    "UV_TOOL_BIN_DIR",
)

_LINE_COMMENT_TOKENS = ("#", "//", ";", "*", "REM ", "rem ")


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

# A drive-letter path rooted in a *named user profile*: the shape that cannot
# survive being committed, because it is true of exactly one machine.
_USER_PROFILE_ABS = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]Users[\\/](?P<user>[^\\/\s\"'`<>|;)]+)")
# POSIX home directories are the same defect on the other platform.
_POSIX_HOME_ABS = re.compile(r"/(?:home|Users)/(?P<user>[A-Za-z0-9._-]+)")


def _file_kind(rel: str) -> str:
    name = Path(rel).name
    if name == "Makefile":
        return "make"
    if name in {".npmrc", ".gitignore"}:
        return "ini"
    return Path(name).suffix


def _is_comment_line(line: str, kind: str) -> bool:
    """True when the line cannot resolve or launch anything."""
    if kind == "make":
        return line.lstrip().startswith("#")
    if kind == "ini":
        return line.lstrip().startswith(("#", ";"))
    if kind in {".ps1", ".psm1", ".sh", ".yaml", ".yml", ".py", ".mjs", ".toml"}:
        return line.lstrip().startswith(_LINE_COMMENT_TOKENS)
    return False


def _code_lines_from(text: str, kind: str) -> list[tuple[int, str]]:
    """Executable lines only. Comments may *name* a machine-specific path, because
    the two documented hazards in this repo are both explained by naming one: a
    profile containing a space, and a path that must not be baked into a
    generated launcher. Keyed on comment *syntax*, not a file allowlist, so the
    exemption cannot quietly grow.

    PowerShell has BOTH ``#`` line comments and ``<# ... #>`` block comments. A
    detector that only knows ``#`` reports every documented hazard inside a block
    comment as a violation, and the first author to hit that deletes the comment
    that was doing the work.
    """
    out: list[tuple[int, str]] = []
    in_block_comment = False

    for lineno, line in enumerate(text.splitlines(), start=1):
        if in_block_comment:
            if "#>" in line:
                in_block_comment = False
            continue

        if kind in {".ps1", ".psm1"}:
            stripped = line.lstrip()
            if stripped.startswith("<#"):
                if "#>" not in stripped:
                    in_block_comment = True
                continue

        if _is_comment_line(line, kind):
            continue

        out.append((lineno, line))

    return out


def _code_lines(rel: str) -> list[tuple[int, str]]:
    return _code_lines_from(read(rel), _file_kind(rel))


# --------------------------------------------------------------------------
# 1. nothing tracked may name a machine
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rel", INSTALL_SURFACE)
def test_no_tracked_launcher_file_names_a_user_profile_on_an_executable_line(rel: str) -> None:
    """A committed path rooted in a user profile is true of one machine only.

    The generated ``scripts/autostart/*.vbs`` launchers legitimately contain the
    installing machine's absolute path, which is why they are gitignored
    (``# Generated by scripts/register_autostart.ps1 - contains this machine's
    paths.``) and why this test only reads tracked files.
    """
    offenders: list[str] = []
    for lineno, line in _code_lines(rel):
        for match in (*_USER_PROFILE_ABS.finditer(line), *_POSIX_HOME_ABS.finditer(line)):
            offenders.append(f"{rel}:{lineno}: {match.group(0)!r}")
    assert not offenders, "machine-specific path on an executable line: " + "; ".join(offenders)


def test_the_user_profile_detector_is_not_vacuous() -> None:
    """Positive control: the scan above can actually see a user profile.

    Without this, a detector whose regex silently stopped matching would let
    every case in this module pass forever by having stopped looking.
    """
    planted = r"C:\Users\PREM KUMAR\Videos\alpha\.tools\bin\uv.exe"
    found = [m.group("user") for m in _USER_PROFILE_ABS.finditer(planted)]
    assert found == ["PREM"], f"detector must recover the offending profile name, got {found!r}"

    posix = "/home/someone/alpha/.tools/bin/uv"
    assert [m.group("user") for m in _POSIX_HOME_ABS.finditer(posix)] == ["someone"]

    # A path that is *not* rooted in a profile must not match, or the gate would
    # be unsatisfiable and someone would eventually disable it.
    for innocent in (r"C:\Program Files\nodejs\node.exe", r".\scripts\toolchain.ps1", "alpha/tools"):
        assert not _USER_PROFILE_ABS.search(innocent), f"false positive on {innocent!r}"


def test_comment_documentation_of_a_spaced_profile_is_allowed() -> None:
    """The exemption is real, and it holds inside a ``<# ... #>`` block comment."""
    assert _is_comment_line('#   ("C:\\Users\\John Doe\\...") would truncate.', ".ps1")

    # A real executable line carrying that path is still an offender.
    code = '    $uv = "C:\\Users\\John Doe\\Alpha\\uv.exe"'
    assert not _is_comment_line(code, ".ps1")
    assert _USER_PROFILE_ABS.search(code)

    docstring = "\n".join(
        [
            "function Get-AlphaNodeWellKnownDirs {",
            "    <#",
            "      nvm names its directory after the version it holds",
            "      (`%APPDATA%\\nvm\\v22.22.2`), so a pinned candidate resolves",
            "      only on the machine that happens to have exactly that one.",
            "    #>",
            '    $dirs = @("$env:ProgramFiles\\nodejs")',
            "}",
        ]
    )
    assert not [ln for _, ln in _code_lines_from(docstring, ".ps1") if "v22.22.2" in ln], "a PowerShell block comment must be treated as a comment, or every documented hazard inside one is reported as a violation"
    assert [ln for _, ln in _code_lines_from(docstring, ".ps1") if "nodejs" in ln]


def test_generated_autostart_launchers_are_gitignored_with_a_reason() -> None:
    """The one place an absolute path is correct is generated, and stays untracked.

    ``register_autostart.ps1`` bakes the installing machine's real path into the
    VBS launchers, because a scheduled task has no other way to find the repo.
    That is correct at generation time and wrong in version control, so the
    pattern must be ignored *and* must say why.
    """
    ignore = read(".gitignore")
    assert "scripts/autostart/*.vbs" in ignore
    assert "register_autostart.ps1" in ignore, "the ignore rule must name its generator"


# --------------------------------------------------------------------------
# 2. nothing tracked may name another product's private tool directory
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rel", TOOLCHAIN_FILES)
@pytest.mark.parametrize("vendor", FOREIGN_VENDOR_TOOLS)
def test_no_launcher_candidate_list_names_a_foreign_vendor_tool(rel: str, vendor: str) -> None:
    """Listing another product's private bin directory adopts that product's binary.

    Measured on the machine this was written for: ``%LOCALAPPDATA%\\hermes\\bin
    \\uv.exe`` existed, ``%USERPROFILE%\\.local\\bin\\uv.exe`` and
    ``%USERPROFILE%\\.cargo\\bin\\uv.exe`` did not, and ``Get-Command uv``
    resolved to the *hermes* copy -- so Alpha was running another tool's uv.
    """
    offenders = [f"{rel}:{lineno}: {line.strip()!r}" for lineno, line in _code_lines(rel) if vendor in line.lower()]
    assert not offenders, "foreign vendor tool directory in a resolution path: " + "; ".join(offenders)


def test_the_vendor_detector_is_not_vacuous() -> None:
    """Positive control: the scan really would have caught the old line."""
    old_line = '    "$env:LOCALAPPDATA\\hermes\\bin\\uv.exe",'
    assert [ln for _, ln in _code_lines_from(old_line, ".ps1") if "hermes" in ln]
    # ...and a comment naming it is tolerated, so the fix removed the candidate
    # rather than imposing a blanket ban on mentioning the incident.
    assert not [ln for _, ln in _code_lines_from("# removed: hermes\\bin\\uv.exe", ".ps1") if "hermes" in ln]


# --------------------------------------------------------------------------
# 3. resolution must be deterministic, not a recursive profile scan
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rel", TOOLCHAIN_FILES)
def test_no_launcher_recursively_scans_the_user_profile_for_an_executable(rel: str) -> None:
    """A recursive ``Get-ChildItem`` over the profile is slow and non-deterministic.

    ``Resolve-Executable`` used to fall back to scanning ``%LOCALAPPDATA%``,
    ``%ProgramFiles%`` and ``%ProgramFiles(x86)`` two levels deep and taking
    ``Select-Object -First 1``. ``Get-ChildItem`` does not guarantee an order, so
    the same machine could resolve a different ``uv.exe`` after an unrelated
    install, and the scan walks a large part of the profile on every launch.
    """
    offenders = [f"{rel}:{lineno}: {line.strip()!r}" for lineno, line in _code_lines(rel) if "Get-ChildItem" in line and re.search(r"-(Recurse|Depth)\b", line)]
    assert not offenders, "recursive profile scan used to resolve an executable: " + "; ".join(offenders)


def test_the_recursive_scan_detector_is_not_vacuous() -> None:
    """Positive control: the old shape and the new bounded listing are told apart."""
    recursive = '            $hit = Get-ChildItem -Path $root -Filter "$Name.exe" -Recurse -Depth 2 -ErrorAction SilentlyContinue |'
    assert [ln for _, ln in _code_lines_from(recursive, ".ps1") if re.search(r"-(Recurse|Depth)\b", ln)]

    # The replacement enumerates the nvm root one level deep -- bounded and
    # intentional -- and must not be mistaken for the old unbounded scan.
    listing = "        $versioned = Get-ChildItem -LiteralPath $nvmRoot -Directory -ErrorAction SilentlyContinue |"
    assert not [ln for _, ln in _code_lines_from(listing, ".ps1") if re.search(r"-(Recurse|Depth)\b", ln)]


@pytest.mark.parametrize("rel", TOOLCHAIN_FILES)
def test_no_launcher_pins_a_single_nvm_node_version(rel: str) -> None:
    """``%APPDATA%\\nvm\\v22.22.2\\node.exe`` misses every other nvm install.

    nvm's directory is named after the version it is currently holding, so a
    pinned candidate resolves only on the machine that happened to have exactly
    that one. The directory has to be enumerated instead.
    """
    offenders = [f"{rel}:{lineno}: {line.strip()!r}" for lineno, line in _code_lines(rel) if re.search(r"nvm[\\/]v\d", line, re.IGNORECASE)]
    assert not offenders, "pinned nvm version in a resolution path: " + "; ".join(offenders)


def test_the_nvm_detector_is_not_vacuous() -> None:
    """Positive control: the pinned candidate fails, the nvm root and version test pass."""
    old = '    "$env:APPDATA\\nvm\\v22.22.2\\node.exe",'
    assert [ln for _, ln in _code_lines_from(old, ".ps1") if re.search(r"nvm[\\/]v\d", ln, re.IGNORECASE)]

    for fine in ('$nvmRoot = "$env:APPDATA\\nvm"', "Where-Object { $_.Name -match '^v\\d+\\.\\d+\\.\\d+$' }"):
        assert not [ln for _, ln in _code_lines_from(fine, ".ps1") if re.search(r"nvm[\\/]v\d", ln, re.IGNORECASE)]


# --------------------------------------------------------------------------
# 4. everything Alpha downloads stays inside the checkout
# --------------------------------------------------------------------------


def test_the_toolchain_module_pins_every_uv_directory_under_the_repo_root() -> None:
    """Each uv directory must be exported, from the repo root, not merely mentioned.

    ``uv``'s own defaults are all per-user globals -- measured on the machine
    this was written for: ``uv cache dir`` -> ``%LOCALAPPDATA%\\uv\\cache``,
    ``uv python dir`` -> ``%APPDATA%\\uv\\python``, ``uv tool dir`` ->
    ``%APPDATA%\\uv\\tools``. Leaving them unset makes every Alpha clone on the
    machine share one cache, and makes the checkout impossible to delete
    cleanly.
    """
    text = read(TOOLCHAIN_MODULE)
    # Comment lines are excluded from the read check: the module's own docstrings
    # name these variables to explain what they are for, and a detector that
    # flagged the explanation would push the next author to delete it.
    code = "\n".join(line for _, line in _code_lines(TOOLCHAIN_MODULE))
    missing = [var for var in UV_GLOBAL_DEFAULTS if not re.search(rf"^\$env:{var}\s*=", text, re.MULTILINE)]
    assert not missing, f"{TOOLCHAIN_MODULE} never assigns {missing}; they keep uv's per-user global defaults"

    for var in UV_GLOBAL_DEFAULTS:
        exported = re.search(rf"^\$env:{var}\s*=\s*(?P<src>\$Uv\w+)\s*$", text, re.MULTILINE)
        assert exported, f"{var} must be exported from a computed local variable, not a literal"
        # Follow the value back to its origin: it has to be built from
        # $ToolchainRoot, which is itself built from $PSScriptRoot. That is the
        # whole chain that makes the directory portable.
        src = exported.group("src")
        # re.escape matters: the captured name starts with `$`, which is regex
        # end-of-string unless escaped -- without it this can never match and the
        # assertion would be decoration.
        assert re.search(rf"^{re.escape(src)}\s*=\s*Join-Path\s+\$ToolchainRoot\b", text, re.MULTILINE), f"{var} comes from {src}, which is not built from $ToolchainRoot; the directory would not follow the checkout"
        # A *read* of the same variable would reintroduce a stale inherited
        # value, the failure this block exists to prevent. An assignment is fine,
        # so only flag a non-assigning reference.
        assert not re.search(rf"\$env:{var}\b(?!\s*=[^=])", code), f"{TOOLCHAIN_MODULE} reads ${var} instead of only exporting it; a stale inherited value would put that directory back outside the project"

    assert re.search(r"\$PSScriptRoot", text), f"{TOOLCHAIN_MODULE} must derive its root from $PSScriptRoot so the checkout can be cloned, moved or renamed anywhere"
    assert re.search(r"\$ToolchainRoot\s*=\s*Join-Path\s+\(?\s*Split-Path\s+\$PSScriptRoot\b", text), "$ToolchainRoot must be built from $PSScriptRoot, so the tools directory always sits beside this module"


@pytest.mark.parametrize("rel", LAUNCHERS)
def test_a_launcher_inherits_the_pins_instead_of_redeclaring_them(rel: str) -> None:
    """The launchers must dot-source the module and keep no uv pin of their own.

    This is the single-source-of-truth rule. Both launchers used to carry a
    private copy of the ``uv`` candidate list, which is exactly how the
    foreign-vendor candidate ended up duplicated in two files and survived a fix
    applied to only one of them.
    """
    text = read(rel)
    assert re.search(r"^\s*\.\s+.*toolchain\.ps1", text, re.MULTILINE), f"{rel} must dot-source {TOOLCHAIN_MODULE}"
    assert "function Resolve-Executable" not in text, f"{rel} still defines its own Resolve-Executable; the resolution order would then exist in two places and drift again"
    redeclared = [var for var in UV_GLOBAL_DEFAULTS if re.search(rf"^\s*\$env:{var}\s*=", text, re.MULTILINE)]
    assert not redeclared, f"{rel} assigns {redeclared} itself; the pins belong to {TOOLCHAIN_MODULE} so the two launchers cannot disagree about them"


def test_the_project_local_tool_root_is_ignored_by_git() -> None:
    """``.tools/`` holds a downloaded uv and a managed CPython: it must not be tracked."""
    assert re.search(r"(?m)^\.tools/?\s*$", read(".gitignore")), ".gitignore must ignore the project-local .tools/ directory, otherwise the downloaded uv and managed CPython become committable binaries"


def test_pnpm_store_is_pinned_inside_the_project() -> None:
    """pnpm's store defaults to a per-user global; the repo already ignores one.

    ``.gitignore`` lists ``.pnpm-store``, so the repository intends a
    project-local store, but nothing configured it -- measured on this machine:
    the store was ``%LOCALAPPDATA%\\pnpm\\store`` while ``.pnpm-store`` did not
    exist. A relative ``store-dir`` in ``frontend/.npmrc`` is resolved by pnpm
    against the ``.npmrc``'s own directory, so it lands in ``frontend/``.
    """
    match = re.search(r"(?m)^\s*store-dir\s*=\s*(?P<value>.+?)\s*$", read("frontend/.npmrc"))
    assert match is not None, "frontend/.npmrc must set a project-local store-dir; pnpm otherwise writes its content-addressable store into the user profile, shared with every other project on the machine"
    value = match.group("value")
    assert not re.match(r"(?i)^[A-Za-z]:[\\/]", value), f"store-dir={value!r} is absolute; it must be relative"
    assert not value.startswith(("/", "~")), f"store-dir={value!r} must be relative to frontend/"


def test_the_makefile_exports_the_same_project_local_uv_directories() -> None:
    """``make install`` runs ``uv sync`` and pre-commit; both must stay local.

    With the uv environment unset those write to the per-user cache and tool
    directory, so the Make path and the PowerShell path would disagree about
    where Alpha's dependencies live.
    """
    makefile = read("Makefile")
    missing = [var for var in UV_GLOBAL_DEFAULTS if not re.search(rf"^\s*{var}\s*[:?]?=", makefile, re.MULTILINE)]
    assert not missing, f"Makefile never sets {missing}; `make` keeps uv's per-user global defaults"
    for var in UV_GLOBAL_DEFAULTS:
        assert re.search(rf"^export {var}\s*$", makefile, re.MULTILINE), f"{var} must be exported, not just assigned: every recipe runs in its own shell and would not see a Make-local variable"
        assert re.search(rf"^\s*{var}\s*[:?]?=.*\$\(TOOLS_DIR\)", makefile, re.MULTILINE), f"{var} must be built from $(TOOLS_DIR) so it cannot be pointed outside the checkout"


# --------------------------------------------------------------------------
# 5. the shared module the launchers actually dot-source
# --------------------------------------------------------------------------


def test_a_shared_toolchain_module_exists_and_is_dot_sourced() -> None:
    """One place decides where the toolchain lives, or the two launchers drift."""
    assert (REPO_ROOT / "scripts" / "toolchain.ps1").is_file(), f"{TOOLCHAIN_MODULE} must exist as the single source of truth"
    for launcher in LAUNCHERS:
        assert re.search(r"^\s*\.\s+.*toolchain\.ps1", read(launcher), re.MULTILINE), f"{launcher} must dot-source {TOOLCHAIN_MODULE}"


def test_toolchain_prefers_the_project_copy_of_a_tool_over_the_system_one() -> None:
    """A tool Alpha downloaded itself must win over a system-wide install.

    Otherwise a stale system ``uv``/``node`` silently shadows the pinned one and
    the "project-local" guarantee is cosmetic.
    """
    text = read(TOOLCHAIN_MODULE)
    body = text[text.index("function Resolve-AlphaTool") :]
    body = body[: body.index("function Get-AlphaUvWellKnownDirs")]

    local_probe = body.find("Join-Path $UvBinDir")
    path_fallback = body.find("Get-Command")
    wellknown = body.find("foreach ($dir in $WellKnownDirs)")

    assert local_probe != -1, "the project-local directory must be probed at all"
    assert path_fallback != -1, "PATH must remain a fallback"
    assert wellknown != -1, "the conventional directory list must remain a fallback"
    assert local_probe < path_fallback < wellknown, "resolution order must be project-local, then PATH, then conventional directories; a system-wide tool would otherwise shadow the pinned one"


def test_the_toolchain_module_exposes_the_entrypoints_the_launchers_call() -> None:
    """Each launcher calls a function the module actually defines.

    A dot-sourced module missing one of these fails at runtime, inside a
    ``try``/``catch`` that reports a dependency problem -- an honest-looking
    message for a typo.
    """
    module_text = read(TOOLCHAIN_MODULE)
    for launcher in LAUNCHERS:
        text = read(launcher)
        for fn in re.findall(r"\b(Initialize-AlphaToolchain|Resolve-AlphaUv|Resolve-AlphaNode|Install-AlphaUv|Resolve-AlphaTool)\b", text):
            assert re.search(rf"^function {fn}\b", module_text, re.MULTILINE), f"{launcher} calls {fn} but {TOOLCHAIN_MODULE} never defines it"
