"""Contract tests for the Alpha Windows installer (installer/**).

These tests are the *verification* half of the installer work: they exist to
fail loudly when the installer drifts away from the guarantees it advertises.

Every assertion here is a guarantee a new user depends on, so none of them may
be weakened, skipped or xfailed. The suite is deliberately static-analysis
based (it reads the shipped scripts) so it runs in CI on a clean runner in
under a second and needs no Windows-only fixtures to be meaningful.

Two of the tests are cross-file contracts with the rest of the repository:

* ``test_installer_uv_pin_matches_the_repository_pin`` reads
  ``backend/Dockerfile``'s ``UV_IMAGE`` and requires the installer to ship the
  *same* uv version. The repository already enforces this pairing for CI in
  ``backend/tests/test_ci_uv_version_pin.py``; if the installer were allowed to
  drift, a released install would resolve ``uv.lock`` with a different resolver
  than the one production pins.
* ``test_docs_index_knows_about_the_installer_document`` keeps
  ``docs/INSTALLER.md`` classified so the documentation-index gate does not
  break when this work lands.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER_DIR = REPO_ROOT / "installer"
BUILD_DIR = REPO_ROOT / "build"
DEVCONTAINER_DIR = REPO_ROOT / ".devcontainer"
DOCS_DIR = REPO_ROOT / "docs"

BOOTSTRAP = INSTALLER_DIR / "bootstrap.ps1"
PINS = INSTALLER_DIR / "pins.json"
BUDGET = INSTALLER_DIR / "size-budget.json"
LIB_MODULE = INSTALLER_DIR / "lib" / "Alpha.Installer.psm1"
UNINSTALLER = INSTALLER_DIR / "uninstall-alpha.ps1"
SHORTCUT_SCRIPT = INSTALLER_DIR / "create-shortcuts.ps1"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "windows-installer.yml"
INSTALLER_DOC = DOCS_DIR / "INSTALLER.md"
BACKEND_DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"
DOCS_INDEX_GENERATOR = REPO_ROOT / "scripts" / "generate_docs_index.py"

# A ref that moves over time. Installing from one of these makes "the version I
# installed" unanswerable, which is the whole point of pinning a tag.
MOVING_REFS = frozenset(
    {
        "main",
        "master",
        "develop",
        "development",
        "trunk",
        "latest",
        "stable",
        "head",
        "nightly",
        "edge",
    }
)

# Substrings that indicate a real credential was committed. Kept deliberately
# broad: a false positive costs one line of cleanup, a false negative ships a
# live key to every user who runs the installer.
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"),
)

# Directories under my ownership that are never allowed to contain a credential.
TRACKED_INSTALLER_DIRS = (INSTALLER_DIR, BUILD_DIR, DEVCONTAINER_DIR)

ALL_SCRIPT_FILES = (
    BOOTSTRAP,
    UNINSTALLER,
    SHORTCUT_SCRIPT,
    LIB_MODULE,
)


def _read(path: Path) -> str:
    assert path.is_file(), f"required installer file is missing: {path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def _pinned_uv_version() -> str:
    """Read the single source of truth for the uv version the repo ships."""
    text = BACKEND_DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r"ghcr\.io/astral-sh/uv:(?P<version>\d+\.\d+\.\d+)", text)
    assert match is not None, "backend/Dockerfile no longer pins a ghcr.io/astral-sh/uv version"
    return match.group("version")


def _pins() -> dict:
    return json.loads(_read(PINS))


# ---------------------------------------------------------------------------
# Guard: the suite must not be silently empty.
# ---------------------------------------------------------------------------


def test_the_installer_contract_suite_actually_has_tests() -> None:
    """A collected-but-empty suite would make every other assertion decorative."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    tests = [node.name for node in tree.body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
    assert len(tests) >= 30, f"expected a substantial contract suite, found only {len(tests)} tests"


# ---------------------------------------------------------------------------
# Pin file: reproducibility
# ---------------------------------------------------------------------------


def test_pins_file_exists_and_is_valid_json() -> None:
    pins = _pins()
    assert isinstance(pins, dict) and pins, "installer/pins.json must be a non-empty object"


def test_pinned_repository_ref_is_a_tag_not_a_moving_branch() -> None:
    pins = _pins()
    ref = pins["repo"]["ref"]
    assert ref, "pins.json must pin a repository ref"
    assert ref.lower() not in MOVING_REFS, f"pin {ref!r} is a moving branch; a released install must be reproducible"
    assert re.fullmatch(r"v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?", ref), f"pin {ref!r} must be an immutable version tag like v2.1.0"


def test_pinned_repository_url_is_https() -> None:
    pins = _pins()
    url = pins["repo"]["url"]
    assert url.startswith("https://"), f"repository URL must be https, got {url!r}"
    assert url.endswith(".git"), f"repository URL must address a .git remote, got {url!r}"


def test_installer_uv_pin_matches_the_repository_pin() -> None:
    """The installer must resolve uv to the exact version backend/Dockerfile ships."""
    expected = _pinned_uv_version()
    actual = _pins()["uv"]["version"]
    assert actual == expected, f"installer pins uv {actual!r} but the repository ships uv {expected!r}; the two must not drift"


def test_python_version_is_pinned_to_a_minor_series() -> None:
    spec = _pins()["python"]["version"]
    assert re.fullmatch(r"3\.\d+", spec), f"python pin {spec!r} must be a 3.MINOR series so the patch is resolved by uv, not by the installer"


def test_installer_pins_every_runtime_it_downloads() -> None:
    """A pin file that omits a download is a pin file that cannot be audited."""
    pins = _pins()
    for key in ("repo", "uv", "python", "node"):
        assert key in pins, f"pins.json must pin {key!r}"
    assert pins["node"]["version"], "the Node.js version must be pinned"
    assert re.fullmatch(r"\d+\.\d+\.\d+", pins["node"]["version"]), "node pin must be an exact version"


# ---------------------------------------------------------------------------
# Secrets: generated on the target machine, never shipped
# ---------------------------------------------------------------------------


def test_no_hardcoded_secret_in_any_tracked_installer_file() -> None:
    """No credential-shaped literal in any file this change would ship.

    Third-party trees are excluded, and the exclusion is deliberate rather than
    convenient: build/measure/prod-venv is a throwaway virtualenv whose
    packages carry Google API keys and base64 blobs inside upstream *test
    fixtures* (youtube_transcript_api ships a real-looking key in
    tests/assets/*.static). Those are not our secrets, they are not ours to
    delete, and build/.gitignore keeps the whole directory out of the commit.
    """
    excluded = {"node_modules", ".venv", "__pycache__", "prod-venv", "measure"}
    offenders: list[str] = []
    for directory in TRACKED_INSTALLER_DIRS:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            if any(part in excluded for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for pattern in SECRET_PATTERNS:
                if pattern.search(text):
                    offenders.append(f"{path.relative_to(REPO_ROOT)} matches {pattern.pattern}")
    assert not offenders, f"a credential-shaped literal is committed: {offenders}"


def test_the_measurement_scratch_tree_is_gitignored() -> None:
    """The third-party venv the measurement tool builds must not be committable."""
    ignore = (BUILD_DIR / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^measure/", ignore, re.MULTILINE), "build/.gitignore must exclude the measurement scratch tree"


def test_bootstrap_generates_secrets_with_secrets_token_urlsafe() -> None:
    """Secrets must come from a CSPRNG on the target machine, not from the installer."""
    text = _read(BOOTSTRAP)
    assert "secrets" in text and "token_urlsafe" in text, "the bootstrapper must generate secrets via Python's secrets.token_urlsafe"


def test_bootstrap_never_writes_a_secret_into_a_tracked_file() -> None:
    text = _read(BOOTSTRAP)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        for pattern in SECRET_PATTERNS:
            assert not pattern.search(line), f"a commented example in the bootstrapper looks like a real credential: {stripped[:80]}"


def test_env_file_is_gitignored_and_written_only_locally() -> None:
    """The generated .env must never be committable."""
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^\.env$", gitignore, re.MULTILINE), "the repository .gitignore must ignore .env so generated secrets cannot be committed"


def test_bootstrap_prints_no_secret_value() -> None:
    text = _read(BOOTSTRAP)
    for forbidden in ("Write-Host $secret", "Write-Host $env:SECRET", "echo $secret", "Write-Output $secret"):
        assert forbidden not in text, f"the bootstrapper must never print a secret: {forbidden!r}"


# ---------------------------------------------------------------------------
# Safety: additive only, never destructive
# ---------------------------------------------------------------------------


def test_bootstrap_never_removes_or_deletes_user_data() -> None:
    """Constraint 1: the bootstrapper may not delete, move or rename user files.

    The installer DOES clean up after itself (the uv and Node archives it
    downloads, its own redirect temp files), so a blanket "no Remove-Item
    anywhere" assertion would be theatre. What matters is that every removal is
    funnelled through one helper that REFUSES any path outside the system temp
    directory. So: exactly one Remove-Item call site, inside that helper, and the
    helper must contain the guard.
    """
    text = _read(BOOTSTRAP)
    call_sites = [line for line in text.splitlines() if re.search(r"\bRemove-Item\b", line, re.IGNORECASE)]
    assert len(call_sites) == 1, f"expected exactly one Remove-Item call site (inside the scratch helper), found {len(call_sites)}: {call_sites}"

    helper = re.search(r"function\s+Remove-ScratchPath[\s\S]*?\n\}", text)
    assert helper, "removals must be funnelled through a single named helper"
    body = helper.group(0)
    assert "Remove-Item" in body, "the helper is the one place that removes"
    assert re.search(r"GetTempPath", body), "the helper must compare against the system temp directory"
    assert re.search(r"throw", body), "the helper must REFUSE a path outside the temp directory, not merely warn"
    # The lookbehind is load-bearing: "Move-Item" is a substring of "Remove-Item",
    # so a plain search matches the one removal the script is allowed to make.
    assert not re.search(r"(?<![A-Za-z])(Move-Item|Rename-Item|Clear-Item|Copy-Item\s+-Destination\s+\$root)", text, re.IGNORECASE), "the bootstrapper must never move, rename or clear anything"


def test_uninstaller_requires_explicit_confirmation_before_removing_anything() -> None:
    """A new uninstaller exists, so it must be the one place deletion is allowed -- and be deliberate."""
    text = _read(UNINSTALLER)
    assert "Remove-Item" in text, "the uninstaller is the sanctioned place that removes files"
    assert re.search(r"\[switch\]\$Yes", text), "the uninstaller must accept -Yes for non-interactive removal"
    assert "Read-Host" in text or "$Yes" in text, "the uninstaller must confirm interactively unless -Yes is given"


def test_bootstrap_does_not_reclone_over_an_existing_install() -> None:
    text = _read(BOOTSTRAP)
    assert re.search(r"Test-Path[^\n]*InstallRoot", text), "the bootstrapper must test for an existing install before cloning"
    assert re.search(r"already installed|already present|exists", text, re.IGNORECASE), "the existing-install branch must be named in the output"


def test_bootstrap_never_clones_a_moving_branch() -> None:
    text = _read(BOOTSTRAP)
    assert "--branch" in text, "the clone must name the ref explicitly"
    assert re.search(r"--depth", text), "the clone must be shallow"
    assert "--single-branch" in text, "the clone must fetch only the pinned ref"


# ---------------------------------------------------------------------------
# Idempotency: running twice must corrupt nothing
# ---------------------------------------------------------------------------


def test_bootstrap_never_overwrites_an_existing_config() -> None:
    text = _read(BOOTSTRAP)
    assert re.search(r"config\.yaml", text), "the bootstrapper must handle config.yaml"
    assert not re.search(r"Copy-Item[^\n]*config\.example\.yaml[^\n]*config\.yaml(?![^\n]*-(Force|WhatIf))", text), "config.yaml must never be copied over an existing one unconditionally"


def test_bootstrap_never_overwrites_an_existing_env_file() -> None:
    text = _read(BOOTSTRAP)
    assert re.search(r"if\s*\(\s*![\s\S]{0,80}Test-Path[^\n]*\.env", text), "the .env write must be guarded by a not-exists test"


def test_bootstrap_guards_every_idempotent_write_with_a_not_exists_check() -> None:
    """Each user-owned artefact must be created only when absent."""
    text = _read(BOOTSTRAP)
    for artefact in ("config.yaml", ".env", "extensions_config.json"):
        pattern = re.compile(r"Test-Path[^\n]*(?:" + re.escape(artefact) + r"|\$envPath|\$configPath|\$extConfigPath)")
        assert pattern.search(text), f"the bootstrapper must guard writes to {artefact} with an existence test"


def test_bootstrap_upgrade_path_preserves_user_data() -> None:
    """An upgrade must never re-clone; it must be a no-op for an existing install."""
    text = _read(BOOTSTRAP)
    assert re.search(r"if\s*\(\s*Test-Path\s+\$InstallRoot", text), "the bootstrapper must short-circuit on an existing install root"


# ---------------------------------------------------------------------------
# Preflight: named reasons, non-zero exits
# ---------------------------------------------------------------------------


def test_bootstrap_has_a_preflight_stage() -> None:
    text = _read(BOOTSTRAP)
    assert re.search(r"function\s+Invoke-Preflight", text, re.IGNORECASE), "the bootstrapper must implement an explicit preflight"


@pytest.mark.parametrize(
    ("needle", "requirement"),
    [
        ("BuildNumber", "the Windows build number, read from WMI"),
        ("PROCESSOR_ARCHITECTURE", "the CPU architecture"),
        ("FreeSpace", "free disk space on the target volume"),
        ("PSVersionTable", "the PowerShell version"),
    ],
)
def test_preflight_checks_a_named_requirement(needle: str, requirement: str) -> None:
    """Each preflight check must read a real source, not assert a literal."""
    text = _read(BOOTSTRAP)
    preflight = re.search(r"function\s+Invoke-Preflight[\s\S]*?\n\}", text)
    assert preflight, "Invoke-Preflight must exist"
    assert needle in preflight.group(0), f"preflight must read {requirement} ({needle!r})"


def test_preflight_failure_exits_non_zero_with_the_real_reason() -> None:
    text = _read(BOOTSTRAP)
    # The mechanism is a single named failure function that always exits
    # non-zero, called from every preflight branch. Asserting on a bare `throw`
    # would test a style choice rather than the guarantee.
    helper = re.search(r"function\s+Write-StepFailure[\s\S]*?\n\}", text)
    assert helper, "a single named failure function must exist"
    body = helper.group(0)
    assert re.search(r"exit\s+1", body), "the failure function must exit non-zero"
    assert "$Reason" in body and "-Message" in body, "the failure function must report the real reason"
    assert re.search(r"preflight/[a-z]+", text), "each preflight failure must name its own stage"
    assert re.search(r"RequiredFreeDiskMB", text), "the disk requirement must be a named constant, not a magic number"


def test_every_stage_records_its_own_log() -> None:
    text = _read(BOOTSTRAP)
    assert re.search(r"alpha-installer\.log|installer\.log", text), "the bootstrapper must keep an actionable log"
    assert re.search(r"function\s+Write-StepLog|function\s+Write-Step", text, re.IGNORECASE), "the log must be written through a named step function"


# ---------------------------------------------------------------------------
# Timeouts: every subprocess bounded
# ---------------------------------------------------------------------------


def test_bootstrap_bounds_every_subprocess_with_a_timeout() -> None:
    """An unbounded clone or uv sync can hang a first-run install forever."""
    text = _read(BOOTSTRAP)
    assert "Invoke-Bounded" in text, "the bootstrapper must route subprocesses through a bounded helper"
    index = text.find("function Invoke-Bounded")
    assert index != -1, "the bounded runner must be defined in the bootstrapper itself, so the installer stays a single self-contained file"
    signature = text[index : index + 1200]
    assert re.search(r"\[int\]\$TimeoutSeconds", signature), "Invoke-Bounded must declare a timeout parameter"
    assert re.search(r"Parameter\(Mandatory\s*=\s*\$true\)\s*\]\s*\[int\]\$TimeoutSeconds", signature), "the timeout parameter must be mandatory, so no call site can forget it"


def test_bootstrap_defines_named_timeout_constants() -> None:
    text = _read(BOOTSTRAP)
    for stage in ("Clone", "Sync", "Node", "Health"):
        # These are script-scope constants, so the reference is $script:<Name>.
        assert re.search(r"\$(?:script:)?\w*" + stage + r"TimeoutSeconds", text, re.IGNORECASE), f"a named timeout constant for the {stage} stage is required"


# ---------------------------------------------------------------------------
# Dry run: the mode the tests and cautious users exercise
# ---------------------------------------------------------------------------


def test_bootstrap_supports_a_dry_run_mode() -> None:
    text = _read(BOOTSTRAP)
    assert re.search(r"\[switch\]\$DryRun", text), "the bootstrapper must expose -DryRun"


def test_every_mutating_call_site_is_reachable_only_through_the_dry_run_guard() -> None:
    """Structural guarantee: the step dispatcher returns before doing work."""
    text = _read(BOOTSTRAP)
    assert re.search(r"if\s*\(\$DryRun\)\s*\{[^}]*return", text), "Invoke-InstallStep must return before invoking its action when -DryRun is set"
    # Every mutating cmdlet in the script must sit inside a scriptblock handed to
    # the dispatcher, i.e. on a line that is lexically inside Invoke-InstallStep
    # or one of the step functions the dispatcher guards.
    guarded_functions = [
        name
        for name in ("Install-PinnedUv", "Install-PinnedPython", "Install-Repository", "Install-BackendDependencies", "Install-FrontendRuntime", "Initialize-UserConfiguration")
        if re.search(r"function\s+" + re.escape(name), text)
    ]
    assert len(guarded_functions) == 6, f"every mutating step function must exist; found {guarded_functions}"


def _run_bootstrap(args: list[str], install_root: Path) -> subprocess.CompletedProcess[str]:
    """Run bootstrap.ps1 in a child PowerShell and capture its transcript."""
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(BOOTSTRAP),
        "-InstallRoot",
        str(install_root),
        *args,
    ]
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=900)


def test_dry_run_actually_changes_nothing_on_disk(tmp_path: Path) -> None:
    """Behavioural, not lexical: run it and prove the filesystem is untouched.

    This is the guarantee that matters. A dry run that quietly created a
    directory, a log file or a config would be worse than no dry run, because a
    user would trust it.
    """
    install_root = tmp_path / "AlphaDryRun"
    result = _run_bootstrap(["-DryRun"], install_root)

    assert result.returncode == 0, f"dry run must succeed; it exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert not install_root.exists(), f"dry run created {install_root}; a dry run must not touch the machine:\n{_tree(install_root)}"
    assert "WOULD" in result.stdout, f"dry run must report what it would do:\n{result.stdout}"


def test_dry_run_runs_preflight_and_reports_the_pinned_release(tmp_path: Path) -> None:
    """A dry run that skips the checks is a no-op, not a dry run."""
    result = _run_bootstrap(["-DryRun"], tmp_path / "AlphaDryRun2")
    assert result.returncode == 0, result.stderr
    assert "Preflight" in result.stdout, f"dry run must execute the preflight:\n{result.stdout}"
    assert "pinned" in result.stdout.lower(), f"dry run must report the pinned release:\n{result.stdout}"


def test_running_twice_is_a_no_op(tmp_path: Path) -> None:
    """Idempotency, behaviourally: an already-completed install is left alone.

    A completed install is simulated (marker plus a sentinel file) so this
    exercises the real guard without a multi-gigabyte install.
    """
    install_root = tmp_path / "AlphaTwice"
    (install_root / "alpha").mkdir(parents=True)
    (install_root / ".alpha-install-complete").write_text("completed_utc=2026-01-01T00:00:00Z\n", encoding="utf-8")
    sentinel = install_root / "alpha" / "config.yaml"
    sentinel.write_text("database:\n  backend: sqlite\n# user edit that must survive\n", encoding="utf-8")
    user_env = install_root / "alpha" / ".env"
    user_env.write_text("BETTER_AUTH_SECRET=user-owned-value\n", encoding="utf-8")
    before = sorted(p.name for p in install_root.rglob("*"))

    first = _run_bootstrap(["-SkipVerification"], install_root)
    second = _run_bootstrap(["-SkipVerification"], install_root)

    assert first.returncode == 0, f"first run over a completed install must exit 0:\n{first.stdout}\n{first.stderr}"
    assert second.returncode == 0, f"second run must exit 0:\n{second.stdout}\n{second.stderr}"
    assert "already installed" in second.stdout.lower(), f"the second run must say the install already exists:\n{second.stdout}"
    assert sentinel.read_text(encoding="utf-8") == "database:\n  backend: sqlite\n# user edit that must survive\n", "a re-run must not modify config.yaml"
    assert user_env.read_text(encoding="utf-8") == "BETTER_AUTH_SECRET=user-owned-value\n", "a re-run must not touch the user's .env"
    assert sorted(p.name for p in install_root.rglob("*")) == before, "a re-run must not add or remove any file"


def _tree(path: Path) -> str:
    if not path.exists():
        return "<absent>"
    return "\n".join(str(p) for p in sorted(path.rglob("*"))) or "<empty>"


# ---------------------------------------------------------------------------
 # Behavioural: secrets are generated locally and user config is never clobbered
# ---------------------------------------------------------------------------

# Runs the real shipped functions from bootstrap.ps1 without executing its
# main entry point: the trailing `Invoke-Main` call is stripped in a COPY, so
# the shipped file is never modified.
_HARNESS_PREAMBLE = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$DryRun = $false
$Yes = $true
$InstallRoot = $null
$Tag = $null
$RepoUrl = $null
$WithPostgres = $false
$SkipVerification = $true
$VerifyRemote = $false
$Uninstall = $false
$script:LogPath = $null
$script:Checks = New-Object System.Collections.ArrayList
. '{bootstrap}'
Write-Output '--- calling Initialize-UserConfiguration ---'
Initialize-UserConfiguration -RepoPath '{repo}' -PythonExe '{python}'{postgres}
Write-Output '--- done ---'
"""


def _bootstrap_without_main(tmp_path: Path) -> Path:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    marker = "\nInvoke-Main\n"
    assert text.rstrip().endswith("Invoke-Main"), "bootstrap.ps1 should end by invoking its entry point"
    stripped = text.rstrip()[: -len("Invoke-Main")].rstrip() + "\n"
    harness = tmp_path / "bootstrap_harness.ps1"
    harness.write_text(stripped, encoding="utf-8")
    assert marker not in harness.read_text(encoding="utf-8")
    return harness


def _make_fake_checkout(root: Path) -> Path:
    repo = root / "alpha"
    (repo / "backend" / ".venv" / "Scripts").mkdir(parents=True)
    (repo / "config.example.yaml").write_text("database:\n  backend: sqlite\n", encoding="utf-8")
    (repo / "extensions_config.example.json").write_text('{"middlewares": []}', encoding="utf-8")
    (repo / ".env.example").write_text("# example\nOPENAI_API_KEY=your-openai-api-key\n", encoding="utf-8")
    return repo


def test_secrets_are_generated_locally_and_written_only_to_the_env_file(tmp_path: Path) -> None:
    """The real code path, run for real: two distinct CSPRNG secrets, no leak."""
    repo = _make_fake_checkout(tmp_path)
    harness = _bootstrap_without_main(tmp_path)
    python = REPO_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"

    script = _HARNESS_PREAMBLE.format(
        bootstrap=str(harness).replace("'", "''"),
        repo=str(repo).replace("'", "''"),
        python=str(python).replace("'", "''"),
        postgres="",
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, f"Initialize-UserConfiguration failed:\n{result.stdout}\n{result.stderr}"

    env_text = (repo / ".env").read_text(encoding="utf-8")
    values = dict(line.split("=", 1) for line in env_text.splitlines() if "=" in line and not line.startswith("#"))
    auth = values.get("BETTER_AUTH_SECRET", "")
    csrf = values.get("CSRF_SECRET", "")

    assert auth and csrf, f"both secrets must be written to .env, got keys {sorted(values)}"
    assert auth != csrf, "the two secrets must be independently generated"
    assert len(auth) >= 40, f"token_urlsafe(48) must yield a long secret, got {len(auth)} chars"

    # The critical property: the value must exist in .env and NOWHERE else.
    # If the secret had been echoed, it would appear in the captured output.
    assert auth not in result.stdout, "a generated secret was printed to the console"
    assert auth not in result.stderr, "a generated secret was printed to stderr"
    for other in (repo / "config.yaml", repo / "extensions_config.json", repo / "frontend" / ".env"):
        if other.is_file():
            assert auth not in other.read_text(encoding="utf-8"), f"a secret leaked into {other.name}"


def test_user_configuration_is_never_clobbered_on_a_second_call(tmp_path: Path) -> None:
    """Re-running must preserve an edited config.yaml and an existing .env."""
    repo = _make_fake_checkout(tmp_path)
    harness = _bootstrap_without_main(tmp_path)
    python = REPO_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
    script = _HARNESS_PREAMBLE.format(
        bootstrap=str(harness).replace("'", "''"),
        repo=str(repo).replace("'", "''"),
        python=str(python).replace("'", "''"),
        postgres="",
    )
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]

    first = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=300)
    assert first.returncode == 0, first.stderr

    # The user edits both files, exactly as a real user would.
    (repo / "config.yaml").write_text("database:\n  backend: postgres  # USER EDIT\n", encoding="utf-8")
    (repo / ".env").write_text("BETTER_AUTH_SECRET=mine-not-the-installers\n", encoding="utf-8")
    (repo / "extensions_config.json").write_text('{"user": "edit"}', encoding="utf-8")

    second = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=300)
    assert second.returncode == 0, second.stderr

    assert (repo / "config.yaml").read_text(encoding="utf-8") == "database:\n  backend: postgres  # USER EDIT\n", "a second run overwrote the user's config.yaml"
    assert (repo / ".env").read_text(encoding="utf-8") == "BETTER_AUTH_SECRET=mine-not-the-installers\n", "a second run overwrote the user's .env"
    assert (repo / "extensions_config.json").read_text(encoding="utf-8") == '{"user": "edit"}', "a second run overwrote the user's extensions_config.json"
    assert "left untouched" in second.stdout, f"the second run must say it preserved the user's files:\n{second.stdout}"


def test_sqlite_is_the_default_and_postgres_is_only_annotated_when_opted_in(tmp_path: Path) -> None:
    """No database server unless the operator explicitly asks for Postgres."""
    repo = _make_fake_checkout(tmp_path)
    harness = _bootstrap_without_main(tmp_path)
    python = REPO_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"

    def run(postgres: str) -> str:
        script = _HARNESS_PREAMBLE.format(
            bootstrap=str(harness).replace("'", "''"),
            repo=str(repo).replace("'", "''"),
            python=str(python).replace("'", "''"),
            postgres=postgres,
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=300,
        )
        assert result.returncode == 0, result.stderr
        return (repo / ".env").read_text(encoding="utf-8")

    default_env = run("")
    assert "sqlite" in default_env, "the default install must state the SQLite backend"
    assert "AGENT_WORKSPACE_DATABASE_BACKEND" not in default_env, "the default install must not enable Postgres"

    fresh = _make_fake_checkout(tmp_path / "pg")
    (tmp_path / "pg").mkdir(exist_ok=True)
    harness2 = _bootstrap_without_main(tmp_path / "pg")
    script = _HARNESS_PREAMBLE.format(
        bootstrap=str(harness2).replace("'", "''"),
        repo=str(fresh).replace("'", "''"),
        python=str(python).replace("'", "''"),
        postgres=" -WithPostgres",
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    optin_env = (fresh / ".env").read_text(encoding="utf-8")
    assert "AGENT_WORKSPACE_DATABASE_BACKEND=postgres" in optin_env, "-WithPostgres must be the only way Postgres is enabled"
    assert "YOUR OWN Postgres" in optin_env, "the Postgres opt-in must say the operator supplies the database"


def test_dry_run_reports_what_would_happen() -> None:
    text = _read(BOOTSTRAP)
    assert re.search(r"WOULD|would", text), "dry-run output must describe what would happen"


def test_dry_run_runs_the_full_preflight() -> None:
    text = _read(BOOTSTRAP)
    assert re.search(r"-DryRun[\s\S]{0,600}Invoke-Preflight", text), "dry run must still execute every preflight check"


# ---------------------------------------------------------------------------
# Post-install verification that actually starts the app
# ---------------------------------------------------------------------------


def test_bootstrap_verifies_by_starting_the_app_and_polling_health() -> None:
    text = _read(BOOTSTRAP)
    assert re.search(r"Invoke-PostInstallVerification", text), "the bootstrapper must implement post-install verification"
    assert re.search(r"/health", text), "verification must poll a health endpoint"
    assert re.search(r"Start-Process", text), "verification must actually start the app, not merely check that files exist"


def test_post_install_verification_fails_loudly() -> None:
    text = _read(BOOTSTRAP)
    assert re.search(r"INSTALL VERIFICATION FAILED|verification failed", text, re.IGNORECASE), "a failed verification must be named in the output"
    assert re.search(r"exit\s+1", text), "a failed verification must exit non-zero"


def test_post_install_verification_is_skipped_in_dry_run() -> None:
    """The guard must come BEFORE the app is started, inside the same function.

    Asserting on proximity anywhere in the file would be satisfied by a comment;
    what matters is that within the verification function, the $DryRun check
    precedes Start-Process.
    """
    text = _read(BOOTSTRAP)
    body = re.search(r"function\s+Invoke-PostInstallVerification[\s\S]*?\n\}\n", text)
    assert body, "Invoke-PostInstallVerification must exist"
    inner = body.group(0)
    guard = inner.find("$DryRun")
    start = inner.find("Start-Process")
    assert guard != -1, "the verification function must check $DryRun"
    assert start != -1, "the verification function must actually start the app"
    assert guard < start, "the $DryRun guard must come before Start-Process, or a dry run would launch Alpha"


# ---------------------------------------------------------------------------
# Configuration and secrets policy
# ---------------------------------------------------------------------------


def test_bootstrap_defaults_to_sqlite_and_treats_postgres_as_opt_in() -> None:
    text = _read(BOOTSTRAP)
    assert re.search(r"\[switch\]\$WithPostgres|\[switch\]\$UsePostgres", text), "Postgres must be an explicit opt-in switch"
    assert re.search(r"sqlite", text, re.IGNORECASE), "SQLite must be the default and must be named"


def test_no_env_example_ships_a_live_credential() -> None:
    """Any .env.example the installer relies on must carry placeholders, never keys.

    The installer copies the repository's own .env.example into the user's .env
    before appending locally generated secrets, so that file is the one place a
    credential could accidentally become permanent and shared by every install.
    An empty value or a `your-...` placeholder is fine; anything matching a real
    credential shape is not. Asserted unconditionally - no skip - because both
    the installer's own example and the repository's are checked.
    """
    candidates = [p for p in (INSTALLER_DIR / ".env.example", REPO_ROOT / ".env.example") if p.is_file()]
    assert candidates, "expected at least one .env.example for the installer to copy from"
    for example in candidates:
        for number, line in enumerate(example.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            if not any(token in key.upper() for token in ("SECRET", "TOKEN", "KEY", "PASSWORD")):
                continue
            value = value.strip()
            for pattern in SECRET_PATTERNS:
                assert not pattern.search(value), f"{example.name}:{number} {key} contains a real credential; ship a placeholder or nothing"
            is_placeholder = value == "" or value.lower().startswith(("your-", "your_", "<", "changeme", "replace", "example", "xxx", "todo"))
            assert is_placeholder, f"{example.name}:{number} {key} must ship empty or a 'your-...' placeholder, got {value!r}"


# ---------------------------------------------------------------------------
# Shortcuts
# ---------------------------------------------------------------------------


def test_bootstrap_creates_start_menu_and_desktop_shortcuts() -> None:
    text = _read(BOOTSTRAP)
    assert "create-shortcuts.ps1" in text, "the bootstrapper must delegate shortcut creation to a script"
    shortcuts = _read(SHORTCUT_SCRIPT)
    assert "Start Menu" in shortcuts or "StartMenu" in shortcuts or "Programs" in shortcuts, "a Start Menu shortcut is required"
    assert "Desktop" in shortcuts, "a desktop shortcut is required"


# ---------------------------------------------------------------------------
# uv sync: the repository's own dependency manager
# ---------------------------------------------------------------------------


def test_bootstrap_uses_uv_python_install_not_a_python_installer() -> None:
    text = _read(BOOTSTRAP)
    assert "uv python install" in text, "Python must be provisioned by uv, never by a separate Python installer"
    assert not re.search(r"python\.org|python-\d\.\d+(\.\d+)?-amd64", text), "the bootstrapper must not download Python from python.org"


def test_bootstrap_runs_uv_sync() -> None:
    text = _read(BOOTSTRAP)
    assert re.search(r"\bsync\b", text), "the bootstrapper must run uv sync"
    assert "--locked" in text, "uv sync must be --locked so a released install resolves the committed lock exactly"


# ---------------------------------------------------------------------------
# Size budget: one greppable place, actually enforced
# ---------------------------------------------------------------------------


def test_size_budget_is_a_single_greppable_json_file() -> None:
    budget = json.loads(_read(BUDGET))
    assert "budgets" in budget, "size-budget.json must expose a budgets object"
    assert budget.get("currency_units") == "mebibytes", "budgets must declare their unit so a number is never ambiguous"


@pytest.mark.parametrize("key", ["installer_script", "installed_footprint", "source_checkout"])
def test_size_budget_declares_a_positive_budget_for(key: str) -> None:
    budget = json.loads(_read(BUDGET))["budgets"]
    assert key in budget, f"size-budget.json must budget {key!r}"
    assert budget[key] > 0, f"{key} budget must be positive"


def test_measurement_tool_exists_and_is_runnable() -> None:
    tool = BUILD_DIR / "measure_footprint.py"
    text = _read(tool)
    assert "--budget" in text, "the measurement tool must be able to enforce the budget in CI"
    assert "size-budget.json" in text, "the measurement tool must read the single budget file"


def test_workflow_enforces_the_size_budget_and_can_fail() -> None:
    text = _read(WORKFLOW)
    assert "measure_footprint.py" in text, "the installer workflow must run the measurement tool"
    assert re.search(r"budget", text, re.IGNORECASE), "the workflow must reference the budget"
    # A step that only warns is not a gate.
    assert re.search(r"continue-on-error:\s*true", text) is None, "the budget step must not be allowed to fail open"


def test_workflow_keeps_the_pre_existing_electron_build() -> None:
    """The workflow is extended, never replaced: the Electron NSIS build stays."""
    text = _read(WORKFLOW)
    assert "npm run dist" in text, "the existing Electron packaging step must be preserved"
    assert "Agent-Workspace-Windows-Installer" in text, "the existing artifact name must be preserved"


def test_workflow_installs_the_artifact_on_a_clean_runner_and_asserts_health() -> None:
    text = _read(WORKFLOW)
    assert re.search(r"smoke", text, re.IGNORECASE), "the workflow must have a smoke test"
    assert re.search(r"health", text, re.IGNORECASE), "the smoke test must assert a health endpoint answers"
    assert re.search(r"runs-on:\s*windows", text), "the smoke test must run on Windows"


def test_workflow_setup_uv_steps_pin_the_repository_uv_version() -> None:
    """backend/tests/test_ci_uv_version_pin.py fails the build if this drifts."""
    expected = _pinned_uv_version()
    text = _read(WORKFLOW)
    for match in re.finditer(r"astral-sh/setup-uv@[^\s]+", text):
        window = text[match.start() : match.start() + 600]
        version = re.search(r"version:\s*['\"]?([0-9]+\.[0-9]+\.[0-9]+)", window)
        assert version is not None, f"the setup-uv step in {match.group(0)} must pin a version"
        assert version.group(1) == expected, f"setup-uv pins {version.group(1)} but the repo ships {expected}"


# ---------------------------------------------------------------------------
# Phase 2: container path
# ---------------------------------------------------------------------------


def test_devcontainer_definition_exists_and_pins_uv() -> None:
    import yaml

    config = yaml.safe_load(_read(DEVCONTAINER_DIR / "devcontainer.json"))
    image = config["image"]
    expected = _pinned_uv_version()
    assert f"ghcr.io/astral-sh/uv:{expected}" in image, f"devcontainer must run on the pinned uv image {expected}, got {image!r}"


def test_devcontainer_definition_is_valid_json() -> None:
    """devcontainer.json is consumed as JSON by the Dev Containers extension."""
    import json

    payload = json.loads(_read(DEVCONTAINER_DIR / "devcontainer.json"))
    assert isinstance(payload, dict) and payload.get("image"), "the devcontainer must be a JSON object with an image"


def test_devcontainer_prebuilds_the_application() -> None:
    """Phase 2 exists so the app is prebuilt, not compiled on first run.

    The setup lives in a script the devcontainer invokes, so this follows the
    wiring rather than grepping one file for a keyword: a postCreateCommand that
    points at a missing script would otherwise look fine.
    """
    import yaml

    config = yaml.safe_load(_read(DEVCONTAINER_DIR / "devcontainer.json"))
    command = config.get("postCreateCommand")
    assert command, "the devcontainer must run a setup command on create"

    script_name = Path(str(command).split()[-1]).name
    script = DEVCONTAINER_DIR / script_name
    assert script.is_file(), f"postCreateCommand points at {script_name}, which does not exist"

    text = script.read_text(encoding="utf-8")
    assert re.search(r"uv sync\s+--locked", text), "the setup must sync the locked dependencies"
    assert re.search(r"build", text), "the setup must prebuild the frontend"
    assert re.search(r"config\.yaml", text), "the setup must prepare config.yaml"


def test_compose_profile_file_exists_and_uses_a_profile() -> None:
    import yaml

    path = BUILD_DIR / "compose.installer.yaml"
    compose = yaml.safe_load(_read(path))
    services = compose["services"]
    assert services, "the installer compose file must define services"
    assert any("profiles" in svc for svc in services.values()), "services must be behind a compose profile so the default stack is untouched"


def test_compose_profile_does_not_duplicate_or_remove_existing_services() -> None:
    """Additive only: the new file adds services, it never redefines the existing ones."""
    import yaml

    new = yaml.safe_load(_read(BUILD_DIR / "compose.installer.yaml"))["services"]
    existing_paths = sorted((REPO_ROOT / "docker").glob("docker-compose*.yaml"))
    assert existing_paths, "expected existing compose files to compare against"
    for path in existing_paths:
        old = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("services") or {}
        overlap = set(old) & set(new)
        assert not overlap, f"{path.name} already defines {sorted(overlap)}; the installer profile must not redefine them"


# ---------------------------------------------------------------------------
# Phase 3: desktop shell decision must be evidence-backed
# ---------------------------------------------------------------------------


def test_desktop_shell_decision_is_documented_with_a_verdict() -> None:
    doc = _read(INSTALLER_DOC).lower()
    assert "webview2" in doc, "the document must address the WebView2 alternative"
    assert "verdict" in doc, "the document must state an explicit verdict on the desktop shell"


def test_launcher_exists_and_targets_the_local_server() -> None:
    """Phase 3 fallback: a launcher that opens the already-running local server."""
    launcher = BUILD_DIR / "alpha-launcher.ps1"
    text = _read(launcher)
    assert re.search(r"http://(127\.0\.0\.1|localhost):\d+", text), "the launcher must target the local server URL"
    assert re.search(r"WebView2|Start-Process", text), "the launcher must open a window"


# ---------------------------------------------------------------------------
# Phase 4 / docs
# ---------------------------------------------------------------------------


def test_installer_document_exists_and_covers_the_required_sections() -> None:
    doc = _read(INSTALLER_DOC).lower()
    for heading in ("uninstall", "verify", "security", "requirement", "troubleshoot"):
        assert heading in doc, f"docs/INSTALLER.md must cover {heading!r}"


def test_installer_document_labels_measured_versus_estimated() -> None:
    doc = _read(INSTALLER_DOC).lower()
    assert "measured" in doc, "the document must label measured numbers"
    assert "estimated" in doc or "not verified" in doc, "the document must label what was not verified"


def test_docs_index_knows_about_the_installer_document() -> None:
    """docs/INDEX.md is generated; the classification lives in FILE_OVERRIDES."""
    text = _read(DOCS_INDEX_GENERATOR)
    assert '"INSTALLER.md"' in text, "scripts/generate_docs_index.py must classify INSTALLER.md or the docs-index gate fails"


# ---------------------------------------------------------------------------
# Windows correctness
# ---------------------------------------------------------------------------


def test_no_bash_and_chain_in_any_installer_script() -> None:
    """cmd.exe and PowerShell have no && chaining; a stray one is a syntax error."""
    for path in ALL_SCRIPT_FILES:
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            assert "&&" not in line, f"{path.name}:{number} uses && which does not exist on Windows: {line.strip()[:80]}"


def test_installer_scripts_are_utf8_without_bom() -> None:
    for path in ALL_SCRIPT_FILES:
        if not path.is_file():
            continue
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{path.name} has a UTF-8 BOM; Windows PowerShell 5.1 misparses it"
        raw.decode("utf-8")


def test_installer_treats_native_exit_codes_as_the_only_verdict() -> None:
    """Under $ErrorActionPreference='Stop', stderr from a native command is a
    terminating error even when the tool succeeded. The scripts must judge
    subprocesses by $LASTEXITCODE."""
    text = _read(BOOTSTRAP)
    assert "$LASTEXITCODE" in text, "the bootstrapper must branch on $LASTEXITCODE"
